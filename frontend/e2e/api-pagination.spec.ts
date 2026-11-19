import { expect, test, type Page } from "@playwright/test";

async function openIsolatedClient(page: Page) {
  await page.route("**/api/v1/**", (route) => route.fulfill({ status: 401, json: { error: { message: "Authentication required" } } }));
  await page.goto("/");
}

test("array collection loading reaches older records, preserves filters, and deduplicates overlapping IDs", async ({ page }) => {
  await openIsolatedClient(page);
  const records = Array.from({ length: 205 }, (_, index) => ({ id: `record-${index}` }));
  records[100] = { id: "record-99" };
  const requests: Array<{ offset: number; limit: number; status: string | null; environment: string | null }> = [];
  await page.route("**/api/v1/pagination-records?**", (route) => {
    const params = new URL(route.request().url()).searchParams;
    const offset = Number(params.get("offset"));
    const limit = Number(params.get("limit"));
    requests.push({ offset, limit, status: params.get("status"), environment: params.get("environment_id") });
    return route.fulfill({ json: records.slice(offset, offset + limit) });
  });
  const ids = await page.evaluate(async () => {
    const { apiGetAll } = await import("/src/lib/api.ts");
    const records = await apiGetAll<{ id: string }>("/pagination-records?status=failed&environment_id=staging&limit=30&offset=70");
    return records.map((record) => record.id);
  });
  expect(ids).toHaveLength(204);
  expect(new Set(ids).size).toBe(204);
  expect(ids.at(-1)).toBe("record-204");
  expect(requests).toEqual([0, 100, 200].map((offset) => ({ offset, limit: 100, status: "failed", environment: "staging" })));
});

for (const repeatedPage of ["full", "partial"]) {
  test(`array collection loading rejects an ignored offset that repeats a ${repeatedPage} page`, async ({ page }) => {
    await openIsolatedClient(page);
    let requests = 0;
    await page.route("**/api/v1/pagination-repeated?**", (route) => {
      requests += 1;
      const records = [{ id: "first" }, { id: "second" }, { id: "third" }];
      return route.fulfill({ json: requests > 1 && repeatedPage === "partial" ? records.slice(0, 1) : records });
    });
    const error = await page.evaluate(async () => {
      const { apiGetAll } = await import("/src/lib/api.ts");
      try { await apiGetAll("/pagination-repeated", undefined, 3); return "Unexpected success"; }
      catch (caught) { return caught instanceof Error ? caught.message : String(caught); }
    });
    expect(error).toContain("repeated a collection page");
    expect(requests).toBe(2);
  });
}

test("service options include entries beyond the first hundred and stop when the total shrinks", async ({ page }) => {
  await openIsolatedClient(page);
  const requests: number[] = [];
  await page.route("**/api/v1/pagination-services?**", (route) => {
    const params = new URL(route.request().url()).searchParams;
    const offset = Number(params.get("offset"));
    requests.push(offset);
    expect(params.get("owner_team_id")).toBe("payments");
    const total = offset ? 150 : 205;
    const items = Array.from({ length: Math.min(100, total - offset) }, (_, index) => ({ id: `service-${offset + index}`, name: `Service ${offset + index}` }));
    return route.fulfill({ json: { items, total, limit: 100, offset } });
  });
  const catalog = await page.evaluate(async () => {
    const { apiGetAllServices } = await import("/src/lib/api.ts");
    return apiGetAllServices("/pagination-services?owner_team_id=payments");
  });
  expect(requests).toEqual([0, 100]);
  expect(catalog.items).toHaveLength(150);
  expect(catalog.items.at(-1)?.id).toBe("service-149");
  expect(catalog.total).toBe(150);
  expect(catalog.offset).toBe(0);
});

test("service collection loading rejects a server that repeats a partial page", async ({ page }) => {
  await openIsolatedClient(page);
  let requests = 0;
  await page.route("**/api/v1/pagination-services-repeated?**", (route) => {
    requests += 1;
    return route.fulfill({ json: { items: [{ id: "repeated-service" }], total: 5, limit: 100, offset: 0 } });
  });
  const error = await page.evaluate(async () => {
    const { apiGetAllServices } = await import("/src/lib/api.ts");
    try { await apiGetAllServices("/pagination-services-repeated"); return "Unexpected success"; }
    catch (caught) { return caught instanceof Error ? caught.message : String(caught); }
  });
  expect(error).toContain("repeated a collection page");
  expect(requests).toBe(2);
});

test("an already aborted collection load never starts a request", async ({ page }) => {
  await openIsolatedClient(page);
  let requests = 0;
  await page.route("**/api/v1/pagination-aborted?**", (route) => { requests += 1; return route.fulfill({ json: [] }); });
  const errorName = await page.evaluate(async () => {
    const { apiGetAll } = await import("/src/lib/api.ts");
    const controller = new AbortController();
    controller.abort();
    try { await apiGetAll("/pagination-aborted", controller.signal); return "Unexpected success"; }
    catch (caught) { return caught instanceof Error ? caught.name : String(caught); }
  });
  expect(errorName).toBe("AbortError");
  expect(requests).toBe(0);
});
