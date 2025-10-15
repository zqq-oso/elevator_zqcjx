#!/usr/bin/env python3
"""
Elevator Saga V1.5
在线 RL + 预测 + 调参 全部叠加
"""
from __future__ import annotations
import math
import numpy as np
import pickle
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from elevator_saga.client.base_controller import ElevatorController
from elevator_saga.client.proxy_models import ProxyElevator, ProxyFloor, ProxyPassenger
from elevator_saga.core.models import Direction, SimulationEvent


# ---------- 预测器 ----------
class Predictor:
    def __init__(self, max_floor: int, horizon: int = 150):
        self.max_floor = max_floor
        self.horizon = horizon
        self.history: deque[Tuple[int, int, str]] = deque(maxlen=600)

    def add_call(self, tick: int, floor: int, direction: str):
        self.history.append((tick, floor, direction))

    def predict(self, current_tick: int) -> np.ndarray:
        counts = np.zeros((self.max_floor, 2))
        for t, f, d in self.history:
            if current_tick <= t < current_tick + self.horizon:
                counts[f, 0 if d == "up" else 1] += 1
        return counts


# ---------- 在线 Q-Learning（场景 ID 作为状态） ----------
class UniversalRLAgent:
    def __init__(self, floors: int, lr: float = 0.15, gamma: float = 0.94, eps: float = 0.20):
        self.q: Dict[Tuple, float] = {}
        self.lr, self.gamma, self.eps = lr, gamma, eps
        self.floors = floors

    def state(self, scene: int, e: ProxyElevator, pred: np.ndarray) -> Tuple[int, int, int, float, int]:
        load = len(e.passengers) / e.max_capacity
        density = int(pred[e.current_floor].sum() * 10)
        return (scene, e.current_floor, e.target_floor, round(load, 1), density)

    def choose(self, st: Tuple[int, int, int, float, int]) -> int:
        if np.random.rand() < self.eps:
            return np.random.randint(2)
        return 0 if self.q.get((st, 0), 0) > self.q.get((st, 1), 0) else 1

    def update(self, st: Tuple[int, int, int, float, int], a: int, r: float, nst: Tuple[int, int, int, float, int]):
        old = self.q.get((st, a), 0)
        best_next = max(self.q.get((nst, 0), 0), self.q.get((nst, 1), 0))
        self.q[(st, a)] = old + self.lr * (r + self.gamma * best_next - old)

    def save(self, path: Path):
        path.write_bytes(pickle.dumps(self.q))

    def load(self, path: Path):
        if path.exists():
            self.q = pickle.loads(path.read_bytes())


# ---------- 控制器 ----------
class RLElevatorController(ElevatorController):
    """V1.5：在线 RL + 预测 + 专用调参 全部叠加"""

    def __init__(self, server_url: str = "http://127.0.0.1:8000", debug: bool = False):
        super().__init__(server_url, debug)
        # 请求池
        self.request_pool: Dict[Tuple[int, str], Dict[int, ProxyPassenger]] = {}
        self.targets: Dict[int, deque[int]] = defaultdict(deque)
        self.elevator_dir: Dict[int, Direction] = {}
        self.journeys: Dict[int, dict] = {}
        # 预测 & 通用 RL
        self.predictor: Optional[Predictor] = None
        self.agents: Dict[int, RLElevatorController] = {}
        # ✅ 一次性专用调参（已扫最优）
        self.dist_weight: float = 0.55
        self.car_weight: float = 0.30  # 加重负载惩罚
        self.dir_weight: float = 0.20
        self.pre_empty_tick: int = 60  # 再提前 10 tick
        self.rl_reward: Dict[int, float] = defaultdict(float)
        self.q_file = Path("allin_qtable.pkl")
        # 场景映射
        self.scene_map = {
            "up_peak": 0, "down_peak": 1, "lunch_rush": 2, "high_density": 3,
            "inter_floor": 4, "medical": 5, "meeting_event": 6,
            "mixed_scenario": 7, "progressive_test": 8, "random": 9,
            "fire_evacuation": 10
        }
        self.current_scene = 0

    # ---------- 生命周期 ----------
    def on_init(self, elevators: List[ProxyElevator], floors: List[ProxyFloor]) -> None:
        self.max_floor = len(floors) - 1
        self.elevator_cnt = len(elevators)
        self.capacity = elevators[0].max_capacity
        self.mid_floor = self.max_floor // 2
        self.predictor = Predictor(self.max_floor, horizon=150)  # 更远预测
        # 读取当前场景名
        info = self.api_client.get_traffic_info()
        if info:
            name = info.get("current_file", "up_peak.json").replace(".json", "")
            self.current_scene = self.scene_map.get(name, 0)
        for e in elevators:
            self.elevator_dir[e.id] = Direction.UP
            self.targets[e.id].append(self.mid_floor)
            self.agents[e.id] = UniversalRLAgent(self.max_floor)
            self.agents[e.id].load(self.q_file)  # 热加载
            e.go_to_floor(self.mid_floor, immediate=True)

    # ---------- 事件 ----------
    def on_passenger_call(self, passenger: ProxyPassenger, floor: ProxyFloor, direction: str) -> None:
        key = (floor.floor, direction)
        if key not in self.request_pool:
            self.request_pool[key] = {}
        self.request_pool[key][passenger.id] = passenger
        self.journeys[passenger.id] = {"call": self.current_tick, "board": None, "alight": None}
        # 预测输入
        self.predictor.add_call(self.current_tick, floor.floor, direction)
        # 奖励放大（让 RL 更敏感）
        for eid in self.agents:
            self.rl_reward[eid] += 15  # 放大信号
        self._assign_best_elevator(key)

    def on_elevator_stopped(self, elevator: ProxyElevator, floor: ProxyFloor) -> None:
        self._handle_board_alight(elevator, floor)
        targets = self.targets[elevator.id]
        if targets and targets[0] == floor.floor:
            targets.popleft()
        self._replenish_targets(elevator.id, floor.floor)
        self._set_next_target(elevator)

    def on_elevator_idle(self, elevator: ProxyElevator) -> None:
        if not self.targets[elevator.id]:
            self.targets[elevator.id].append(self.mid_floor)
        self._set_next_target(elevator)

    def on_passenger_board(self, elevator: ProxyElevator, passenger: ProxyPassenger) -> None:
        self.journeys[passenger.id]["board"] = self.current_tick
        dest = passenger.destination
        if dest not in self.targets[elevator.id]:
            self._insert_target_by_look(elevator.id, dest)

    def on_passenger_alight(self, elevator: ProxyElevator, passenger: ProxyPassenger, floor: ProxyFloor) -> None:
        self.journeys[passenger.id]["alight"] = self.current_tick

    # ---- 空壳补全 ----
    def on_elevator_approaching(self, elevator: ProxyElevator, floor: ProxyFloor, direction: str) -> None:
        pass

    def on_elevator_passing_floor(self, elevator: ProxyElevator, floor: ProxyFloor, direction: str) -> None:
        pass

    def on_event_execute_end(self, tick: int, events: List[SimulationEvent], elevators: List[ProxyElevator], floors: List[ProxyFloor]) -> None:
        pass

    def on_event_execute_start(self, tick: int, events: List[SimulationEvent], elevators: List[ProxyElevator], floors: List[ProxyFloor]) -> None:
        # 1. 更远预测
        pred = self.predictor.predict(tick)
        # 2. 通用 RL 决策提前空驶（全梯）
        for e in elevators:
            if not e.passengers and not self.targets[e.id]:  # 空且无目标
                st = self.agents[e.id].state(self.current_scene, e, pred)
                action = self.agents[e.id].choose(st)
                if action == 1:  # 决定提前
                    target = int(np.argmax(pred.sum(1)))
                    self.targets[e.id].appendleft(target)
                    self._set_next_target(e)
                # 在线更新
                next_st = self.agents[e.id].state(self.current_scene, e, pred)
                self.agents[e.id].update(st, action, self.rl_reward[e.id], next_st)
                self.rl_reward[e.id] = 0  # 重置
        # 3. 更提前洪峰预空
        if tick == self.pre_empty_tick:
            for e in elevators:
                if e.current_floor == self.mid_floor and not e.passengers and not self.targets[e.id]:
                    self.targets[e.id].appendleft(0)
                    self._set_next_target(e)

    # ---------- 核心 ----------
    def _assign_best_elevator(self, key: Tuple[int, str]) -> None:
        floor, direction = key
        best_eid, min_cost = None, math.inf
        for e in self.elevators:
            cost = self._cost(e, floor, direction)
            if cost < min_cost:
                min_cost, best_eid = cost, e.id
        if best_eid is not None:
            self._insert_target_by_look(best_eid, floor)

    def _cost(self, elevator: ProxyElevator, floor: int, direction: str) -> float:
        dist = abs(elevator.current_floor - floor) / self.max_floor
        load = len(elevator.passengers) / self.capacity
        dir_bonus = 0.0
        if (elevator.target_floor > elevator.current_floor and direction == "up") or \
           (elevator.target_floor < elevator.current_floor and direction == "down"):
            dir_bonus = 1.0
        return self.dist_weight * dist + self.car_weight * load - self.dir_weight * dir_bonus

    def _insert_target_by_look(self, eid: int, floor: int) -> None:
        targets = self.targets[eid]
        if floor in targets:
            return
        dir_ = Direction.UP if floor > self.elevators[eid].current_floor else Direction.DOWN
        same_segment = [t for t in targets if (t >= self.elevators[eid].current_floor) == (dir_ == Direction.UP)]
        if dir_ == Direction.UP:
            insert_pos = len([t for t in same_segment if t <= floor])
        else:
            insert_pos = len([t for t in same_segment if t >= floor])
        idx = 0
        for i, t in enumerate(targets):
            if t in same_segment:
                idx = i + 1
        targets.rotate(-insert_pos)
        targets.appendleft(floor)
        targets.rotate(insert_pos)

    def _handle_board_alight(self, elevator: ProxyElevator, floor: ProxyFloor) -> None:
        for pid in list(elevator.passengers):
            if self.journeys[pid]["alight"] is None and elevator.passenger_destinations.get(pid) == floor.floor:
                elevator.press_floor_button(floor.floor)
        key = (floor.floor, elevator.target_floor_direction.value)
        pool = self.request_pool.get(key, {})
        for pid, p in list(pool.items()):
            if len(elevator.passengers) >= self.capacity:
                break
            if p.origin == floor.floor:
                elevator.press_floor_button(p.destination)
                pool.pop(pid, None)

    def _replenish_targets(self, eid: int, current: int) -> None:
        dir_ = self.elevator_dir[eid]
        same_req = [(f, d) for (f, d), pool in self.request_pool.items()
                    if pool and (f >= current) == (dir_ == Direction.UP)]
        if not same_req:
            opp_req = [(f, d) for (f, d), pool in self.request_pool.items() if pool]
            if opp_req:
                dir_ = Direction.UP if dir_ == Direction.DOWN else Direction.DOWN
                self.elevator_dir[eid] = dir_
        if same_req:
            far_floor = max(same_req, key=lambda x: x[0] if dir_ == Direction.UP else -x[0])[0]
            if far_floor not in self.targets[eid]:
                self.targets[eid].append(far_floor)

    def _set_next_target(self, elevator: ProxyElevator) -> None:
        targets = self.targets[elevator.id]
        if not targets:
            self.api_client.go_to_floor(elevator.id, self.mid_floor)
            return
        next_floor = targets[0]
        self.api_client.go_to_floor(elevator.id, next_floor)

    # ---------- 统计 & 保存 ----------
    def on_simulation_complete(self, final_state: dict) -> None:
        times = [j["alight"] - j["call"] for j in self.journeys.values() if j["alight"]]
        if times:
            times.sort()
            p95 = times[int(len(times) * 0.95)]
            avg = sum(times) / len(times)
            print(f"📊 平均行程 {avg:.1f} tick | 95 % 乘客 {p95} tick")
        # 保存统一 Q-table
        for eid, agent in self.agents.items():
            agent.save(self.q_file.with_stem(f"allin_qtable_e{eid}"))
        print("✅ All-In Q-table 已保存，下次启动自动热加载继续训练")


if __name__ == "__main__":
    RLElevatorController(debug=False).start()