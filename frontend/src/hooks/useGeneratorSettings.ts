import { useEffect, useState } from "react";
import { getGeneratorSettings, getModels } from "../api/client";

export interface UseGeneratorSettingsResult {
  models: string[];
  selectedModel: string;
  temperature: number | null;
  numCtx: number | null;
  handleModelChange: (model: string) => Promise<void>;
  setSelectedModel: (model: string) => void;
}

export function useGeneratorSettings(): UseGeneratorSettingsResult {
  const [models, setModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [temperature, setTemperature] = useState<number | null>(null);
  const [numCtx, setNumCtx] = useState<number | null>(null);

  useEffect(() => {
    getModels().then(setModels).catch(() => {});
    getGeneratorSettings()
      .then((d) => {
        const m = d.models?.default ?? d.model ?? "";
        if (m) setSelectedModel(m);
        if (d.temperature != null) setTemperature(d.temperature);
        if (d.num_ctx != null) setNumCtx(d.num_ctx);
      })
      .catch(() => {});
  }, []);

  async function handleModelChange(model: string) {
    setSelectedModel(model);
    try {
      const current = await getGeneratorSettings();
      await fetch("/api/settings/generator", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...current, models: { ...(current.models ?? {}), default: model } }),
      });
    } catch {
      // best-effort
    }
  }

  return { models, selectedModel, temperature, numCtx, handleModelChange, setSelectedModel };
}
