import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EffortShares } from "./shared";

describe("EffortShares", () => {
  it("lists each project's hours and share, marking internal codes", () => {
    const html = renderToStaticMarkup(<EffortShares rows={[
      { projectNumber: "TPL149", projectName: "TPL149-接口项目", internal: false, hours: "352.00", sharePercent: "72.1" },
      { projectNumber: "TSG999", projectName: null, internal: true, hours: "88.00", sharePercent: "18.0" },
    ]} />);
    expect(html).toContain("TPL149-接口项目");
    expect(html).toContain("352.00 小时");
    expect(html).toContain("72.1%");
    expect(html).toContain("内部");
    expect(html).toContain("width:72.1%");
  });

  it("shows a dash when there is no effective time", () => {
    expect(renderToStaticMarkup(<EffortShares rows={[]} />)).toContain("—");
  });
});
