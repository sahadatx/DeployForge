import http from "k6/http";
import { check, sleep } from "k6";

const baseUrl = (__ENV.BASE_URL || "").replace(/\/$/, "");
const workspaceId = __ENV.WORKSPACE_ID || "";
const cookieName = __ENV.SESSION_COOKIE_NAME || "deployforge_session";
const sessionCookie = __ENV.SESSION_COOKIE || "";
const virtualUsers = Number.parseInt(__ENV.VUS || "20", 10);

if (!baseUrl || !workspaceId || !sessionCookie) {
  throw new Error("Set BASE_URL, WORKSPACE_ID, and SESSION_COOKIE before running this profile.");
}
if (!Number.isSafeInteger(virtualUsers) || virtualUsers < 1 || virtualUsers > 100) {
  throw new Error("VUS must be an integer from 1 through 100.");
}

export const options = {
  stages: [
    { duration: "1m", target: virtualUsers },
    { duration: "3m", target: virtualUsers },
    { duration: "1m", target: 0 },
  ],
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<750", "p(99)<1500"],
    checks: ["rate>0.99"],
  },
};

const api = `${baseUrl}/api/v1/workspaces/${workspaceId}`;
const headers = {
  Accept: "application/json",
  Cookie: `${cookieName}=${sessionCookie}`,
};
const readPaths = [
  ["services", `${api}/services?limit=50`],
  ["deployments", `${api}/deployments?limit=50`],
  ["build_runs", `${api}/build-runs?limit=50`],
  ["gitops_runs", `${api}/gitops-runs?limit=50`],
  ["policies", `${api}/policies?limit=50`],
  ["slos", `${api}/slos?limit=50`],
];

export default function () {
  const responses = http.batch(
    readPaths.map(([name, url]) => ({
      method: "GET",
      url,
      params: { headers, tags: { endpoint: name } },
    })),
  );
  for (let index = 0; index < responses.length; index += 1) {
    const response = responses[index];
    check(response, {
      [`${readPaths[index][0]} returns 200`]: (item) => item.status === 200,
    });
  }
  sleep(1);
}
