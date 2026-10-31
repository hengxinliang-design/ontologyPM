// The two Node APIs the stylesheet tests use (src/typography.test.ts); the app itself does not depend on Node types.
declare module "node:fs" {
  export function readFileSync(path: string, encoding: "utf8"): string;
}
declare module "node:url" {
  export function fileURLToPath(url: URL): string;
}
