// The Worker that starts the network's daily runs on time and checks that they
// finish. The logic is in scheduler.js: this module may export only the
// handler, since Workers treat any other export as an entry point.
import { onSchedule } from "./scheduler.js";

export default {
  // Awaited, not left to waitUntil, so a failure marks the Cron Trigger's run as failed in the logs.
  async scheduled(controller, env) {
    await onSchedule(controller, env);
  },
};
