// On-time agent runs: GitHub's own cron can start hours late, so this worker starts the agent
// workflow itself one minute after every 5m candle close (minutes 1, 6, 11, ... 56 UTC; every
// 15 minutes until 2026-11-08, DECISIONS #150).
// Needs the AGENT_DISPATCH_TOKEN secret (a GitHub token allowed to run Actions on the repo);
// without it nothing is sent and the GitHub cron alone keeps running the agent.
export const DISPATCH_MINUTES = [1, 6, 11, 16, 21, 26, 31, 36, 41, 46, 51, 56];

export function dispatchDue(now) {
  return DISPATCH_MINUTES.includes(new Date(now).getUTCMinutes());
}

export async function dispatchAgent(env, now, doFetch = fetch) {
  if (!env.AGENT_DISPATCH_TOKEN || !dispatchDue(now)) return { dispatched: false };
  const r = await doFetch(
    `https://api.github.com/repos/${env.AGENT_REPO}/actions/workflows/${env.AGENT_WORKFLOW}/dispatches`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${env.AGENT_DISPATCH_TOKEN}`,
        accept: "application/vnd.github+json",
        "user-agent": "trade-news-worker",
        "content-type": "application/json",
      },
      body: JSON.stringify({ ref: "main" }),
    });
  if (r.status !== 204) console.log(`agent dispatch failed: HTTP ${r.status}`);
  return { dispatched: r.status === 204, status: r.status };
}
