import { expect, test } from "@playwright/test";

test("el deslizador cambia el rango de la leyenda", async ({ page }) => {
  await page.goto("/");
  const legend = page.locator("#legend");
  await expect(legend).toContainText("más de 235");

  await page.locator("#mes-inicio").evaluate((el: HTMLInputElement) => {
    el.value = "8";
    el.dispatchEvent(new Event("input"));
  });
  await expect(legend).toContainText("más de 90");
});

test("clic en un distrito muestra su total", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#legend")).toContainText("más de 235");

  const point = await page.evaluate(() => {
    const map = (window as { __map: { project(c: [number, number]): { x: number; y: number } } }).__map;
    return map.project([-77.1, -12.0]);
  });
  await page.mouse.click(point.x, point.y);

  const popup = page.locator(".maplibregl-popup");
  await expect(popup).toContainText("LIMA");
  await expect(popup).toContainText("235 denuncias");
  await expect(popup.locator("svg polyline")).toBeVisible();
});
