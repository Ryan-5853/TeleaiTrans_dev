import { defineStore } from "pinia";

import { api } from "@/api";
import type {
  Aircraft,
  EventItem,
  FlashJob,
  ResultMessage,
  Telemetry
} from "@/types";

interface UiMessage {
  type: string;
  payload: Record<string, unknown>;
}

let socket: WebSocket | null = null;

export const useFleetStore = defineStore("fleet", {
  state: () => ({
    aircraft: [] as Aircraft[],
    telemetry: {} as Record<string, Telemetry>,
    events: [] as EventItem[],
    jobs: {} as Record<string, FlashJob>,
    connected: false,
    lastResult: null as ResultMessage | null
  }),
  actions: {
    async refreshAircraft() {
      const data = await api.aircraft.list();
      this.replaceAircraft(data.aircraft);
    },
    replaceAircraft(list: Aircraft[]) {
      this.aircraft = list;
      for (const item of list) {
        if (item.telemetry) this.telemetry[item.id] = item.telemetry;
      }
    },
    upsertAircraft(item: Aircraft) {
      const index = this.aircraft.findIndex((a) => a.id === item.id);
      if (index >= 0) this.aircraft.splice(index, 1, item);
      else this.aircraft.push(item);
      if (item.telemetry) this.telemetry[item.id] = item.telemetry;
    },
    applyTelemetry(aircraftId: string, telemetry: Telemetry) {
      this.telemetry[aircraftId] = telemetry;
    },
    pushEvent(event: EventItem) {
      this.events = [
        event,
        ...this.events.filter((item) => item.id !== event.id)
      ].slice(0, 500);
    },
    upsertJob(job: FlashJob) {
      this.jobs[job.id] = job;
    },
    connect() {
      if (
        socket &&
        (socket.readyState === WebSocket.OPEN ||
          socket.readyState === WebSocket.CONNECTING)
      ) {
        return;
      }
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${protocol}://${window.location.host}/ui/v1/ws`);
      socket.onopen = () => {
        this.connected = true;
      };
      socket.onclose = () => {
        this.connected = false;
        socket = null;
        window.setTimeout(() => this.connect(), 3000);
      };
      socket.onmessage = (event) => {
        let message: UiMessage;
        try {
          message = JSON.parse(event.data as string) as UiMessage;
        } catch {
          return;
        }
        this.handleMessage(message);
      };
    },
    handleMessage(message: UiMessage) {
      const payload = message.payload ?? {};
      if (message.type === "snapshot") {
        this.replaceAircraft(payload.aircraft as Aircraft[]);
      } else if (message.type === "aircraft_update") {
        this.upsertAircraft(payload.aircraft as Aircraft);
      } else if (message.type === "telemetry") {
        const id = String(payload.aircraft_id);
        const nested = (payload as { payload?: Telemetry }).payload;
        this.applyTelemetry(id, {
          ...(nested ?? (payload as Telemetry)),
          received_ts: Date.now() / 1000
        });
      } else if (message.type === "event") {
        this.pushEvent(payload as unknown as EventItem);
      } else if (message.type === "flash_job") {
        this.upsertJob((payload as { job: FlashJob }).job);
      } else if (message.type === "result") {
        this.lastResult = payload as unknown as ResultMessage;
      }
    },
    disconnect() {
      if (socket) {
        socket.close();
        socket = null;
      }
    }
  }
});
