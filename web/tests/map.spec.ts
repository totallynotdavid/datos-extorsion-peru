import { expect, test } from "@playwright/test";

test("la vista por defecto muestra la tasa y el deslizador la recalcula", async ({
  page,
}) => {
  await page.goto("/");
  const legend = page.locator("#legend");
  await expect(legend).toContainText("por 100 000 habitantes");
  await expect(legend).toContainText("más de 90");
  await expect(legend).toContainText("menos de 1 000 hab.");

  await page.locator("#mes-inicio").evaluate((el: HTMLInputElement) => {
    el.value = "8";
    el.dispatchEvent(new Event("input"));
  });
  await expect(legend).toContainText("más de 110");
});

test("el conmutador cambia la leyenda a denuncias", async ({ page }) => {
  await page.goto("/");
  const legend = page.locator("#legend");
  await expect(legend).toContainText("más de 90");

  await page.locator("#medida button[data-medida='denuncias']").click();
  await expect(legend).toContainText("denuncias en el periodo");
  await expect(legend).toContainText("más de 235");
  await expect(legend).not.toContainText("1 000 hab.");
});

test("clic en un distrito muestra conteo, tasa y población", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator("#legend")).toContainText("más de 90");

  const point = await page.evaluate(() => {
    const map = (window as { __map: { project(c: [number, number]): { x: number; y: number } } }).__map;
    return map.project([-77.1, -12.0]);
  });
  await page.mouse.click(point.x, point.y);

  const popup = page.locator(".maplibregl-popup");
  await expect(popup).toContainText("LIMA");
  await expect(popup).toContainText("235 denuncias");
  await expect(popup).toContainText("78 por 100 000 hab.");
  await expect(popup).toContainText("habitantes");
  await expect(popup.locator("svg polyline")).toBeVisible();
});
