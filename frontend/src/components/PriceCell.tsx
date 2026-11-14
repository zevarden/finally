"use client";
import { useEffect, useRef, useState } from "react";
import { price as fmtPrice } from "@/lib/format";

export const FLASH_MS = 220;

/** Price text that flashes green/red when the value changes, then fades via CSS transition. */
export default function PriceCell({ ticker, value }: { ticker: string; value: number | null | undefined }) {
  const prev = useRef<number | null | undefined>(value);
  const [flash, setFlash] = useState<"" | "flash-up" | "flash-down">("");

  useEffect(() => {
    const before = prev.current;
    prev.current = value;
    if (before == null || value == null || before === value) return;
    setFlash(value > before ? "flash-up" : "flash-down");
    const t = setTimeout(() => setFlash(""), FLASH_MS);
    return () => clearTimeout(t);
  }, [value]);

  return (
    <span data-testid={`price-${ticker}`} className={`price-cell num inline-block px-1 ${flash}`}>
      {fmtPrice(value)}
    </span>
  );
}
