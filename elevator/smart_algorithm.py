#!/usr/bin/env python3
"""
SAGA优化电梯调度算法（完整优化版）
支持场景自适应、动态参数调整和全链路容错
"""
import csv
import time
import socket
import json
from pathlib import Path
from typing import List, Optional, Dict
from elevator_saga.client.base_controller import ElevatorController
from elevator_saga.client.proxy_models import ProxyElevator, ProxyFloor, ProxyPassenger
from elevator_saga.core.models import Direction


class PassengerLog:
    """乘客行程日志记录器，支持完整生命周期追踪"""
    def __init__(self) -> None:
        self.records: Dict[int, Dict] = {}
        self.scenario_name: str = "unknown"
        self.max_wait_tick: int = 200  # 可配置的超时阈值

    def set_scenario(self, name: str) -> None:
        self.scenario_name = name

    def set_max_wait_tick(self, tick: int) -> None:
        """动态调整超时阈值（适配不同场景）"""
        self.max_wait_tick = tick

    def appear(self, pid: int, tick: int, floor: int, dest: int, elevators: List[ProxyElevator]) -> None:
        """记录乘客出现事件"""
        waiting = [f"{rec['from_floor']}→{rec['to_floor']}" 
                  for rec in self.records.values() if rec["board_tick"] == ""]
        waiting_str = " | ".join(waiting) if waiting else "无"

        snapshot = " | ".join(
            f"E{e.id}:F{e.current_floor}→{e.target_floor} "
            f"{e.last_tick_direction.value if e.last_tick_direction else 'STOP'} "
            f"{len(e.passengers)}/{self._get_elevator_capacity(e)}人"
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
        """记录乘客上车事件"""
        if pid in self.records:
            self.records[pid]["board_tick"] = tick

    def alight(self, pid: int, tick: int) -> None:
        """记录乘客下车事件"""
        if pid in self.records:
            self.records[pid]["alight_tick"] = tick

    def finalize(self, current_tick: int) -> None:
        """最终化日志，标记超时未完成行程"""
        for rec in self.records.values():
            if not rec["board_tick"]:
                rec["miss_reason"] = f"未接-超时(>{self.max_wait_tick})"
            elif not rec["alight_tick"]:
                rec["miss_reason"] = f"未下-超时(>{self.max_wait_tick})"

    def save_csv(self, log_dir: Path = Path("logs")) -> None:
        """保存日志到CSV文件（自动创建日志目录）"""
        self.finalize(0)
        log_dir.mkdir(exist_ok=True)  # 确保日志目录存在
        file_name = f"{self.scenario_name}_passenger_log.csv"
        file_path = log_dir / file_name

        try:
            with file_path.open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "passenger_id", "from_floor", "to_floor",
                    "appear_tick", "board_tick", "alight_tick", "miss_reason",
                    "waiting_passengers", "elevator_snapshot"
                ])
                writer.writeheader()
                writer.writerows(self.records.values())
            print(f"[CSV] 已保存日志: {file_path.absolute()}")
        except Exception as e:
            print(f"[CSV] 保存失败: {e}")

    @staticmethod
    def _get_elevator_capacity(elevator: ProxyElevator) -> int:
        """获取电梯容量（兼容不同模拟器版本）"""
        try:
            return elevator.max_passengers
        except AttributeError:
            try:
                return elevator.capacity
            except AttributeError:
                return 8  # 默认容量


class Controller(ElevatorController):
    """优化版电梯控制器，支持场景自适应和动态参数调整"""
    def __init__(self):
        super().__init__("http://127.0.0.1:8001", debug=False)
        self.max_floor: int = 0
        self.log = PassengerLog()
        self.floor_requests: Dict[Direction, set] = {
            Direction.UP: set(),
            Direction.DOWN: set()
        }
        self.elevator_targets: Dict[int, Dict[Direction, set]] = {}  # {电梯ID: {方向: 目标楼层集合}}
        self.floor_request_time: Dict[Direction, Dict[int, int]] = {  # 记录请求产生时间
            Direction.UP: {},
            Direction.DOWN: {}
        }
        
        # 核心算法参数（可根据场景动态调整）
        self.params = {
            "distance_weight": 1.0,    # 距离权重
            "direction_weight": 0.8,   # 方向权重
            "load_weight": 0.5,        # 负载权重
            "wait_time_weight": 0.4,   # 等待时间权重
            "urgency_threshold": 50,   # 紧急请求阈值（tick）
            "urgency_multiplier": 1.5  # 紧急请求放大系数
        }
        
        # 系统参数
        self.command_interval: float = 0.1  # 命令发送间隔（秒）
        self.last_command_time: float = 0   # 上次命令发送时间
        self.elevator_capacities: Dict[int, int] = {}  # 电梯容量缓存

    def on_init(self, elevators: List[ProxyElevator], floors: List[ProxyFloor]) -> None:
        """初始化电梯和楼层信息"""
        self.max_floor = len(floors) - 1
        
        # 初始化电梯数据
        for e in elevators:
            self.elevator_targets[e.id] = {
                Direction.UP: set(),
                Direction.DOWN: set()
            }
            self.elevator_capacities[e.id] = self._get_elevator_capacity(e)
            
            # 分散初始化位置（避免集中在同一楼层）
            try:
                start_floor = (e.id * self.max_floor) // len(elevators)
                self._send_command_with_interval(e.go_to_floor, start_floor, immediate=True)
                time.sleep(0.02)  # 分散请求
            except Exception as e_cmd:
                print(f"[初始化] 电梯{e.id}命令失败: {e_cmd}")

        # 加载场景并调整参数
        scenario_name = self._load_scenario_safely()
        self.log.set_scenario(scenario_name)
        self._adjust_params_for_scenario(scenario_name)
        print(f"[场景] 加载完成: {scenario_name}，参数已适配")

    def _load_scenario_safely(self) -> str:
        """安全加载场景名（全流程容错）"""
        # 尝试获取traffic信息
        traffic_info = self._get_traffic_info_safely()
        if not traffic_info or "current_index" not in traffic_info:
            return "unknown"
        
        # 尝试查找场景文件
        traffic_dir = self._get_traffic_dir_safely()
        if not traffic_dir:
            return "unknown"
            
        scenario_file = self._get_scenario_file_safely(traffic_dir, traffic_info["current_index"])
        if not scenario_file:
            return "unknown"
            
        # 尝试解析场景名
        return self._parse_scenario_name_safely(scenario_file) or "unknown"

    def _adjust_params_for_scenario(self, scenario: str) -> None:
        """根据场景类型动态调整算法参数"""
        if scenario == "morning_peak":  # 早高峰（上行密集）
            self.params["distance_weight"] = 0.8
            self.params["wait_time_weight"] = 0.6
            self.log.set_max_wait_tick(250)  # 放宽超时阈值
        elif scenario == "evening_peak":  # 晚高峰（下行密集）
            self.params["distance_weight"] = 0.8
            self.params["wait_time_weight"] = 0.6
            self.log.set_max_wait_tick(250)
        elif scenario == "off_peak":  # 平峰（分散请求）
            self.params["load_weight"] = 0.7  # 更重视负载均衡
            self.log.set_max_wait_tick(180)
        elif scenario == "high_traffic":  # 高流量（密集请求）
            self.params["urgency_threshold"] = 30  # 更快触发紧急权重
            self.params["urgency_multiplier"] = 2.0
            self.command_interval = 0.08  # 稍微提高命令频率
        # 其他场景使用默认参数

    def _send_command_with_interval(self, command_func, *args, **kwargs) -> None:
        """带间隔发送命令，避免高频请求导致连接崩溃"""
        current_time = time.time()
        wait_time = self.command_interval - (current_time - self.last_command_time)
        if wait_time > 0:
            time.sleep(wait_time)
        
        try:
            command_func(*args, **kwargs)
            self.last_command_time = time.time()
        except Exception as e:
            print(f"[命令] 发送失败: {e}")
            self.last_command_time = time.time() + self.command_interval * 2  # 失败后延长间隔

    def on_passenger_call(self, passenger: ProxyPassenger, floor: ProxyFloor, direction: str) -> None:
        """处理乘客呼叫事件"""
        req_dir = Direction.UP if direction.lower() == "up" else Direction.DOWN
        self.floor_requests[req_dir].add(floor.floor)
        self.floor_request_time[req_dir][floor.floor] = self.current_tick  # 记录请求时间
        
        # 分配最优电梯
        try:
            self.assign_elevator(floor.floor, req_dir, passenger.destination)
        except Exception as e:
            print(f"[呼叫处理] 分配电梯失败: {e}")
        
        # 记录日志
        self.log.appear(passenger.id, self.current_tick, floor.floor, passenger.destination, self.elevators)
        print(f"[乘客] {passenger.id:2d} 呼叫: {floor.floor}→{passenger.destination} ({direction})")

    def assign_elevator(self, floor: int, req_dir: Direction, dest: int) -> None:
        """为请求分配最优电梯"""
        best_elevator: Optional[ProxyElevator] = None
        min_cost = float('inf')
        
        for elevator in self.elevators:
            try:
                cost = self.calculate_cost(elevator, floor, req_dir, dest)
                if cost < min_cost:
                    min_cost = cost
                    best_elevator = elevator
            except Exception as e:
                print(f"[成本计算] 电梯{elevator.id}失败: {e}")
        
        if best_elevator:
            try:
                self.elevator_targets[best_elevator.id][req_dir].add(floor)
            except Exception as e:
                print(f"[分配目标] 电梯{best_elevator.id}失败: {e}")

    def calculate_cost(self, elevator: ProxyElevator, req_floor: int, req_dir: Direction, dest: int) -> float:
        """计算电梯响应请求的综合成本"""
        current_floor = elevator.current_floor
        
        # 1. 距离成本：当前楼层到请求楼层的距离
        distance = abs(current_floor - req_floor)
        distance_cost = distance * self.params["distance_weight"]
        
        # 2. 方向成本：同方向/反方向/静止的惩罚
        elevator_dir = elevator.last_tick_direction
        if elevator_dir == req_dir:
            dir_cost = 0.5  # 同方向优先
        elif elevator_dir is None:
            dir_cost = 1.0  # 静止次之
        else:
            dir_cost = 2.0  # 反方向惩罚
        dir_cost *= self.params["direction_weight"]
        
        # 3. 负载成本：乘客越少优先级越高
        capacity = self.elevator_capacities.get(elevator.id, 8)
        load_ratio = len(elevator.passengers) / capacity if capacity > 0 else 0
        load_cost = load_ratio * self.params["load_weight"] * 10  # 放大负载影响
        
        # 4. 等待时间成本：请求等待越久优先级越高
        wait_time = self.current_tick - self.floor_request_time[req_dir].get(req_floor, self.current_tick)
        wait_time_cost = (wait_time / 100) * self.params["wait_time_weight"]
        
        # 紧急请求放大
        if wait_time > self.params["urgency_threshold"]:
            wait_time_cost *= self.params["urgency_multiplier"]
        
        # 总成本 = 各因素加权和
        return distance_cost + dir_cost + load_cost + wait_time_cost

    def on_elevator_stopped(self, elevator: ProxyElevator, floor: ProxyFloor) -> None:
        """处理电梯停靠事件（更新目标和请求）"""
        current_floor = floor.floor
        try:
            # 移除已完成的目标
            for dir in [Direction.UP, Direction.DOWN]:
                if current_floor in self.elevator_targets[elevator.id][dir]:
                    self.elevator_targets[elevator.id][dir].remove(current_floor)
                if current_floor in self.floor_requests[dir]:
                    self.floor_requests[dir].remove(current_floor)
                    self.floor_request_time[dir].pop(current_floor, None)  # 清理请求时间
        except Exception as e:
            print(f"[状态清理] 电梯{elevator.id}失败: {e}")
        
        # 设置下一个目标
        self.set_next_target(elevator)

    def set_next_target(self, elevator: ProxyElevator) -> None:
        """为电梯设置下一个目标楼层"""
        current_floor = elevator.current_floor
        up_targets = sorted(self.elevator_targets[elevator.id][Direction.UP])
        down_targets = sorted(self.elevator_targets[elevator.id][Direction.DOWN], reverse=True)
        current_dir = elevator.last_tick_direction

        next_floor: Optional[int] = None
        # 优先处理当前方向的目标
        if current_dir == Direction.UP and up_targets:
            next_floor = min([f for f in up_targets if f > current_floor], default=None)
        elif current_dir == Direction.DOWN and down_targets:
            next_floor = max([f for f in down_targets if f < current_floor], default=None)
        
        # 当前方向无目标，切换方向
        if next_floor is None:
            if up_targets:
                next_floor = min(up_targets)
            elif down_targets:
                next_floor = max(down_targets)
            else:
                next_floor = self.max_floor // 2  # 无目标时返回中间楼层
        
        if next_floor is not None and next_floor != current_floor:
            self._send_command_with_interval(elevator.go_to_floor, next_floor)

    # 乘客上下车事件处理
    def on_passenger_board(self, elevator: ProxyElevator, passenger: ProxyPassenger) -> None:
        """处理乘客上车事件"""
        try:
            dest = passenger.destination
            current = elevator.current_floor
            if dest > current:
                self.elevator_targets[elevator.id][Direction.UP].add(dest)
            else:
                self.elevator_targets[elevator.id][Direction.DOWN].add(dest)
        except Exception as e:
            print(f"[上车处理] 电梯{elevator.id}失败: {e}")
        
        self.log.board(passenger.id, self.current_tick)
        print(f"[乘客] {passenger.id:2d} 上车: 电梯{elevator.id} (当前{elevator.current_floor})")

    def on_passenger_alight(self, elevator: ProxyElevator, passenger: ProxyPassenger, floor: ProxyFloor) -> None:
        """处理乘客下车事件"""
        self.log.alight(passenger.id, self.current_tick)
        print(f"[乘客] {passenger.id:2d} 下车: 楼层{floor.floor} (电梯{elevator.id})")

    # 容错与辅助方法
    def _get_elevator_capacity(self, elevator: ProxyElevator) -> int:
        """获取电梯容量（兼容处理）"""
        try:
            return elevator.max_passengers
        except AttributeError:
            try:
                return elevator.capacity
            except AttributeError:
                print(f"[容量获取] 电梯{elevator.id}无属性，使用默认值8")
                return 8

    def _get_traffic_dir_safely(self) -> Optional[Path]:
        """安全获取traffic目录（多路径尝试）"""
        possible_paths = [
            Path(__file__).parent / "elevator_saga" / "traffic",
            Path(__file__).parent / "traffic",
            Path.cwd() / "elevator_saga" / "traffic",
        ]
        for path in possible_paths:
            if path.exists() and path.is_dir():
                return path
        return None

    def _get_traffic_info_safely(self) -> Optional[dict]:
        """安全获取traffic信息（异常捕获）"""
        try:
            return self.api_client.get_traffic_info()
        except Exception as e:
            print(f"[Traffic信息] 获取失败: {e}")
            return None

    def _get_scenario_file_safely(self, traffic_dir: Path, idx: int) -> Optional[Path]:
        """安全匹配场景文件（索引越界保护）"""
        try:
            json_files = sorted([f for f in traffic_dir.glob("*.json") if f.is_file()])
            if not json_files:
                return None
            if idx < 0 or idx >= len(json_files):
                print(f"[场景文件] 索引{idx}越界，使用第一个文件")
                return json_files[0] if json_files else None
            return json_files[idx]
        except Exception as e:
            print(f"[场景文件] 匹配失败: {e}")
            return None

    def _parse_scenario_name_safely(self, scenario_file: Path) -> Optional[str]:
        """安全解析场景名（键存在性检查）"""
        try:
            with open(scenario_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data.get("building"), dict) and "scenario" in data["building"]:
                return str(data["building"]["scenario"]).strip()
            return None
        except Exception as e:
            print(f"[场景解析] {scenario_file.name}失败: {e}")
            return None

    # 空方法占位（满足父类接口）
    def on_event_execute_start(self, tick, events, elevators, floors): 
        time.sleep(0.01)  # 降低循环频率
        pass
    def on_event_execute_end(self, tick, events, elevators, floors): pass
    def on_elevator_idle(self, elevator: ProxyElevator) -> None:
        try:
            self.on_elevator_stopped(elevator, ProxyFloor(elevator.current_floor, self.api_client))
        except Exception as e:
            print(f"[空闲处理] 电梯{elevator.id}失败: {e}")
    def on_elevator_passing_floor(self, elevator, floor, direction): pass
    def on_elevator_approaching(self, elevator, floor, direction): pass
    def on_elevator_move(self, elevator, from_pos, to_pos, direction, status): pass

    # 停止处理与资源释放
    def on_stop(self) -> None:
        """停止时清理资源并保存日志"""
        try:
            # 停止所有电梯（避免残留命令）
            for elevator in self.elevators:
                self._send_command_with_interval(elevator.go_to_floor, elevator.current_floor)
            time.sleep(0.1)
        except Exception as e:
            print(f"[停止清理] 失败: {e}")
        
        # 保存日志到elevator/logs目录
        log_dir = Path(__file__).parent / "logs"
        self.log.save_csv(log_dir)

    # 连接异常重试
    def start(self) -> None:
        """启动控制器（带连接重试）"""
        max_retries = 2
        retry_count = 0
        while retry_count < max_retries:
            try:
                super().start()
                break
            except (ConnectionResetError, socket.error) as e:
                retry_count += 1
                if retry_count >= max_retries:
                    raise
                print(f"[连接] 重置错误，第{retry_count}次重试...")
                time.sleep(1)  # 重试间隔
            except Exception as e:
                raise


if __name__ == "__main__":
    print("启动 SAGA优化电梯调度算法")
    try:
        Controller().start()
    except Exception as e:
        print(f"算法运行出错: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("停止 SAGA优化电梯调度算法")