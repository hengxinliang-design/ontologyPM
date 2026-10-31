import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// UI check 2026-10-30: one type scale for the whole app (styles.css :root, --fs-xs … --fs-3xl); 12px is the floor.
// Read from disk: vitest stubs CSS imports (even `?raw`) to empty strings, which would make these checks pass vacuously.
const STYLESHEETS = ["styles.css", "ontology/ontology.css", "department/department.css", "investment/investment.css"];
const read = (file: string) => readFileSync(fileURLToPath(new URL(`./${file}`, import.meta.url)), "utf8");

describe("type scale", () => {
  it("defines the scale with nothing below 12px", () => {
    const sizes = [...read("styles.css").matchAll(/--fs-([a-z0-9]+):\s*(\d+)px/g)].map(match => Number(match[2]));
    expect(sizes).toEqual([12, 13, 14, 16, 18, 20, 24, 28]);
  });

  it.each(STYLESHEETS)("%s sets every font size from the scale", file => {
    const declarations = [...read(file).matchAll(/font-size:\s*([^;}]+)/g)];
    expect(declarations.length).toBeGreaterThan(0);
    const offenders = declarations
      .map(match => match[1].replace("!important", "").trim())
      .filter(value => !/^var\(--fs-(xs|sm|base|md|lg|xl|2xl|3xl)\)$/.test(value) && value !== "inherit");
    expect(offenders).toEqual([]);
  });
});
