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

test("la leyenda separa el cero y pinta cada distrito según su clase", async ({
  page,
}) => {
  await page.goto("/");
  const legend = page.locator("#legend");

  // Five classes: "0", three non-overlapping ranges and "más de 90", plus the
  // grey row for districts without population data.
  const swatches = legend.locator(".swatch");
  await expect(swatches).toHaveCount(6);
  await expect(legend).toContainText("1 – 75");
  await expect(legend).toContainText("76 – 78");
  await expect(legend).toContainText("79 – 90");
  await expect(legend).toContainText("más de 90");

  // Lima rounds to a rate of 78, so its fill is the third class colour.
  const limaFill = await page.evaluate(() => {
    const match = (
      window as unknown as {
        __map: {
          getPaintProperty(l: string, p: string): unknown[];
        };
      }
    ).__map.getPaintProperty("districts-fill", "fill-color");
    return match[match.indexOf("150101") + 1];
  });
  expect(limaFill).toBe("#fd8d3c");
  await expect(swatches.nth(2)).toHaveCSS(
    "background-color",
    "rgb(253, 141, 60)",
  );
});

test("el conmutador cambia la leyenda a denuncias", async ({ page }) => {
  await page.goto("/");
  const legend = page.locator("#legend");
  await expect(legend).toContainText("más de 90");

  await page.locator("#medida button[data-medida='denuncias']").click();
  await expect(legend).toContainText("denuncias en el periodo");
  await expect(legend.locator(".swatch")).toHaveCount(5);
  await expect(legend).toContainText("1 – 54");
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
