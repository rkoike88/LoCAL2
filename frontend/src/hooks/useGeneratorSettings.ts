import { useEffect, useState } from "react";
import { getGeneratorSettings, getModels, updateGeneratorSettings } from "../api/client";

export interface UseGeneratorSettingsResult {
  models: string[];
  selectedModel: string;
  temperature: number | null;
  numCtx: number | null;
  handleModelChange: (model: string) => Promise<void>;
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
        if (d.model) setSelectedModel(d.model);
        if (d.temperature != null) setTemperature(d.temperature);
        if (d.num_ctx != null) setNumCtx(d.num_ctx);
      })
      .catch(() => {});
  }, []);

  async function handleModelChange(model: string) {
    setSelectedModel(model);
    try {
      await updateGeneratorSettings({ model });
    } catch {
      // best-effort
    }
  }

  return { models, selectedModel, temperature, numCtx, handleModelChange };
}
