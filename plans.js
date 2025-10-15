{
  "down_peak"：{
    "initial_positions": [5, 5],
    "initial_direction": "down",
    "priority": "down",
    "capacity_threshold": 0.8,
    "stop_pattern": "every_down",
    "peak_window": [0, 50],
    "tail_window": [50, 200],
    "force_complete_tick": 200
  };
  "up_peak": {
    "initial_positions": [0, 0],
    "initial_direction": "up",
    "priority": "up",
    "capacity_threshold": 0.8,
    "stop_pattern": "every_up",
    "peak_window": [0, 50],
    "tail_window": [50, 200],
    "force_complete_tick": 200
  };
  "random": {
    "initial_positions": ["middle", "middle"],
    "initial_direction": "stopped",
    "priority": "balanced",
    "capacity_threshold": 0.9,
    "stop_pattern": "every",
    "peak_window": [0, 200],
    "tail_window": [0, 200],
    "force_complete_tick": 200
  },
  "lunch_rush": {
    "initial_positions": [1, 2],
    "initial_direction": "stopped",
    "priority": "balanced",
    "capacity_threshold": 0.9,
    "stop_pattern": "every",
    "peak_window": [0, 200],
    "tail_window": [0, 200],
    "force_complete_tick": 200
  },
  "inter_floor": {
    "initial_positions": [2, 4],
    "initial_direction": "stopped",
    "priority": "balanced",
    "capacity_threshold": 0.9,
    "stop_pattern": "every",
    "peak_window": [0, 200],
    "tail_window": [0, 200],
    "force_complete_tick": 200
  },
  "medical": {
    "initial_positions": [0, 0],
    "initial_direction": "up",
    "priority": "up",
    "capacity_threshold": 0.9,
    "stop_pattern": "every",
    "peak_window": [0, 200],
    "tail_window": [0, 200],
    "force_complete_tick": 200
  },
  "meeting_event": {
    "initial_positions": [3, 3],
    "initial_direction": "stopped",
    "priority": "balanced",
    "capacity_threshold": 0.9,
    "stop_pattern": "every",
    "peak_window": [0, 200],
    "tail_window": [0, 200],
    "force_complete_tick": 200
  },
  "mixed_scenario": {
    "initial_positions": [2, 3],
    "initial_direction": "stopped",
    "priority": "balanced",
    "capacity_threshold": 0.9,
    "stop_pattern": "every",
    "peak_window": [0, 200],
    "tail_window": [0, 200],
    "force_complete_tick": 200
  },
  "high_density": {
    "initial_positions": [3, 3],
    "initial_direction": "up",
    "priority": "balanced",
    "capacity_threshold": 1.0,
    "stop_pattern": "every",
    "peak_window": [0, 200],
    "tail_window": [0, 200],
    "force_complete_tick": 200
  },
  "fire_evacuation": {
    "initial_positions": [-1, -1],
    "initial_direction": "down",
    "priority": "down",
    "capacity_threshold": 1.0,
    "stop_pattern": "every",
    "peak_window": [0, 200],
    "tail_window": [0, 200],
    "force_complete_tick": 200
  },
  "progressive_test": {
    "initial_positions": [2, 4],
    "initial_direction": "stopped",
    "priority": "balanced",
    "capacity_threshold": 0.9,
    "stop_pattern": "every",
    "peak_window": [0, 200],
    "tail_window": [0, 200],
    "force_complete_tick": 200
  }
}