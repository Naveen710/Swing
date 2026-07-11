require("dotenv").config();

const { runAlertCycle, startScheduler, validateConfig } = require("./scheduler");

async function main() {
  validateConfig();

  if (process.argv.includes("--run-now")) {
    await runAlertCycle("manual");
    return;
  }

  startScheduler();
}

main().catch((error) => {
  console.error("[alerts] Fatal startup error:", error);
  process.exitCode = 1;
});
