#!/usr/bin/env python3
"""
「11 场景查表控制器」重命名版 - 读表运行，文件名带场景
仅打印乘客三事件，干净输出
"""

import csv
import json
from pathlib import Path
from typing import List
from elevator_saga.client.base_controller import ElevatorController
from elevator_saga.client.proxy_models import ProxyElevator, ProxyFloor, ProxyPassenger
from elevator_saga.core.models import Direction, SimulationEvent, EventType


class PassengerLog:
    def __init__(self) -> None:
        self.records: dict[int, dict] = {}
        self.scenario_name: str = "unknown"

    def set_scenario(self, name: str) -> None:
        self.scenario_name = name

    def appear(self, pid: int, tick: int, floor: int, dest: int, elevators: List[ProxyElevator]) -> None:
        waiting = [f"{rec['from_floor']}→{rec['to_floor']}" for rec in self.records.values() if rec["board_tick"] == ""]
        waiting_str = " | ".join(waiting) if waiting else "无"
        snapshot = " | ".join(
            f"E{e.id}:F{e.current_floor}→{e.target_floor} {e.last_tick_direction.value} {len(e.passengers)}人"
            for e in elevators
        )
        self.records[pid] = {
            "passenger_id": pid,
            "from_floor": floor,
            "to_floor": dest,
            "appear_tick": tick,
            "board_tick": "",
            "alight_tick": "",
            "miss_reason": "",
            "waiting_passengers": waiting_str,
            "elevator_snapshot": snapshot,
        }

    def board(self, pid: int, tick: int) -> None:
        if pid in self.records:
            self.records[pid]["board_tick"] = tick

    def alight(self, pid: int, tick: int) -> None:
        if pid in self.records:
            self.records[pid]["alight_tick"] = tick

    def finalize(self, current_tick: int) -> None:
        max_tick = 200
        for rec in self.records.values():
            if not rec["board_tick"]:
                rec["miss_reason"] = f"未接-超时(>{max_tick})"
            elif not rec["alight_tick"]:
                rec["miss_reason"] = f"未下-超时(>{max_tick})"

    def save_csv(self, file: Path) -> None:
        self.finalize(0)
        file_name = f"{self.scenario_name}_passenger_log.csv"
        with (file.parent / file_name).open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "passenger_id", "from_floor", "to_floor",
                "appear_tick", "board_tick", "alight_tick", "miss_reason",
                "waiting_passengers", "elevator_snapshot"
            ])
            writer.writeheader()
            writer.writerows(self.records.values())
        print(f"[CSV] 已保存 {(file.parent / file_name).absolute()}  （文件名含场景：{self.scenario_name}）")


class YourController(ElevatorController):  # ← 你随意命名
    def __init__(self):
        super().__init__("http://127.0.0.1:8001", debug=False)
        self.max_floor = 0
        self.direction = Direction.UP
        self.log = PassengerLog()

    def on_init(self, elevators: List[ProxyElevator], floors: List[ProxyFloor]) -> None:
        self.max_floor = len(floors) - 1
        for i, e in enumerate(elevators):
            start = (i * self.max_floor) // len(elevators)
            e.go_to_floor(start, immediate=True)

        # ===== 识别场景并打印 =====
        from pathlib import Path
        traffic_dir = Path(__file__).parent / "plans"
        traffic_info = self.api_client.get_traffic_info()
        if traffic_info and "current_index" in traffic_info:
            idx = traffic_info["current_index"]
            files = sorted(traffic_dir.glob("*.json"))
            if idx < len(files):
                current_file = files[idx]
                import json
                data = json.loads(current_file.read_text(encoding="utf-8"))
                scenario = data["building"]["scenario"]
                self.log.set_scenario(scenario)
                print(f"[场景] 当前加载：{scenario} （来自 {current_file.name}）")
            else:
                self.log.set_scenario("unknown")
                print(f"[场景] 索引越界，设为 unknown")
        else:
            self.log.set_scenario("unknown")
            print(f"[场景] 无 traffic 信息，设为 unknown")

    # ---------- 乘客三事件 ----------
    def on_passenger_call(self, passenger: ProxyPassenger, floor: ProxyFloor, direction: str) -> None:
        self.log.appear(passenger.id, self.current_tick, floor.floor, passenger.destination, self.elevators)
        print(f"[乘客] {passenger.id:2d} 出现  {floor.floor}→{passenger.destination} ({direction})")

    def on_passenger_board(self, elevator: ProxyElevator, passenger: ProxyPassenger) -> None:
        self.log.board(passenger.id, self.current_tick)
        print(f"[乘客] {passenger.id:2d} 上车  电梯{elevator.id}  {elevator.current_floor}→{passenger.destination}")

    def on_passenger_alight(self, elevator: ProxyElevator, passenger: ProxyPassenger, floor: ProxyFloor) -> None:
        self.log.alight(passenger.id, self.current_tick)
        print(f"[乘客] {passenger.id:2d} 下车  电梯{elevator.id}  楼层{floor.floor}")

    # ---------- 空方法占位 ----------
    def on_event_execute_start(self, tick, events, elevators, floors): pass
    def on_event_execute_end(self, tick, events, elevators, floors): pass
    def on_elevator_idle(self, elevator: ProxyElevator) -> None:
        self.on_elevator_stopped(elevator, ProxyFloor(elevator.current_floor, self.api_client))
    def on_elevator_stopped(self, elevator: ProxyElevator, floor: ProxyFloor) -> None:
        self.schedule(elevator)
    def on_elevator_passing_floor(self, elevator, floor, direction): pass
    def on_elevator_approaching(self, elevator, floor, direction): pass
    def on_elevator_move(self, elevator, from_pos, to_pos, direction, status): pass

    # ---------- Bus 循环 ----------
    def schedule(self, elevator: ProxyElevator) -> None:
        current = elevator.current_floor
        if current == self.max_floor and self.direction == Direction.UP:
            self.direction = Direction.DOWN
            elevator.go_to_floor(current - 1); return
        if current == 0 and self.direction == Direction.DOWN:
            self.direction = Direction.UP
            elevator.go_to_floor(current + 1); return
        elevator.go_to_floor(current + 1 if self.direction == Direction.UP else current - 1)

    # ---------- 结束保存 ----------
    def on_stop(self) -> None:
        self.log.save_csv(Path("passenger_log.csv"))


if __name__ == "__main__":
    YourController().start()