import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

// lightweight-charts needs canvas; replace it with a recording stub in jsdom.
vi.mock("lightweight-charts", () => {
  const series = () => ({ setData: vi.fn(), update: vi.fn(), applyOptions: vi.fn() });
  return {
    ColorType: { Solid: "solid" },
    CrosshairMode: { Magnet: 1 },
    AreaSeries: "Area",
    BaselineSeries: "Baseline",
    LineSeries: "Line",
    createChart: vi.fn(() => ({
      addSeries: vi.fn(series),
      timeScale: () => ({ fitContent: vi.fn() }),
      applyOptions: vi.fn(),
      remove: vi.fn(),
    })),
  };
});

afterEach(() => cleanup());
