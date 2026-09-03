export interface M0State {
  status?: string;
  app_version?: string;
  crc?: string;
  armed?: boolean;
  active_test?: string | null;
}

export interface BoardState {
  cpu_pct?: number;
  mem_pct?: number;
  temp_c?: number;
}

export interface Telemetry {
  uptime_s?: number;
  board?: BoardState;
  m0?: M0State;
  fcu?: FcuState;
  peripherals?: {
    camera?: { devices?: { device?: string; name?: string | null }[] };
    dock?: { profile?: string | null; state?: string };
  };
  received_ts?: number;
}

export interface FcuState {
  link?: "up" | "down";
  armed?: boolean;
  mode?: string;
  status?: number | null;
  battery?: { v?: number | null; current_a?: number | null; pct?: number | null };
  gps?: { fix?: number; sats?: number; lat?: number; lon?: number };
  attitude?: { roll?: number; pitch?: number; yaw?: number };
  speed?: {
    airspeed_mps?: number;
    groundspeed_mps?: number;
    climb_mps?: number;
  };
}

export interface Aircraft {
  id: string;
  name: string;
  alias?: string;
  model: string;
  channel: "plane-link" | "ssh" | "none";
  proto?: 1 | 2 | null;
  static_ip?: string | null;
  last_ip?: string | null;
  claim_state?: string;
  online: boolean;
  capabilities: string[];
  last_seen?: number | null;
  agent_version?: string;
  m0?: M0State;
  fcu?: FcuState;
  peripherals?: Telemetry["peripherals"];
  ssh?: { host: string; user: string } | null;
  telemetry?: Telemetry;
}

export interface Artifact {
  id: string;
  filename: string;
  size: number;
  sha256: string;
  version: number;
  load_addr: number;
  entry: number;
  crc32: number;
  uploaded_at: number;
}

export interface FlashJob {
  id: string;
  aircraft_id: string;
  artifact_id: string;
  status: "queued" | "running" | "success" | "failed";
  phase: string;
  percent: number;
  error?: string | null;
  log_tail: string;
  started_at?: number;
  finished_at?: number;
}

export interface EventItem {
  id: number;
  ts: number;
  aircraft_id?: string | null;
  level: string;
  code: string;
  message: string;
  data: Record<string, unknown>;
}

export interface ResultMessage {
  aircraft_id: string;
  request_id: string;
  ok: boolean;
  error?: string | null;
  data?: Record<string, unknown>;
}
