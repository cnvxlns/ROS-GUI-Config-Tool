import { ParamType, type NodeDTO, type ParamDTO, type ParamTypeValue } from '../api/types'

export function makeParam(over: Partial<ParamDTO> = {}): ParamDTO {
  return {
    name: 'some_param',
    type: ParamType.STRING as ParamTypeValue,
    description: '',
    additional_constraints: '',
    read_only: false,
    dynamic_typing: false,
    integer_range: null,
    floating_point_range: null,
    runtime_value: null,
    saved_value: null,
    saved_source: null,
    drift: false,
    has_schema: false,
    label: null,
    layer: null,
    apply: 'restart',
    tier: 'engineer',
    enum_values: null,
    global_ref: null,
    lock_while_driving: true,
    ...over,
  }
}

export function makeNode(over: Partial<NodeDTO> = {}): NodeDTO {
  return {
    name: '/ouster/os_driver',
    alive: true,
    registered: true,
    internal: false,
    subsystem: 'lidar_top',
    unit: 'av-lidar-top.service',
    param_files: ['params/lidar_top.yaml', 'params/global.yaml'],
    display_mode: 'mapped',
    schema_driver: 'ouster_ros',
    param_count: 1,
    drift_count: 0,
    downstream: [],
    unreachable_reason: null,
    launch_rule_violation: false,
    ...over,
  }
}
