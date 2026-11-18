import { useCallback, useEffect, useState } from "react";
import {
  activateEngineBuild,
  bindEnginePack,
  createEngineBuild,
  createEngineKb,
  fetchEngineBuild,
  fetchEngineConfig,
  fetchEngineKbs,
  fetchEnginePacks,
  patchEngineConfig,
} from "../lib/engineApi";
import type { EngineBuild, EngineConfigPayload, EngineKB, EnginePack } from "../lib/engineApi";

interface Props {
  className?: string;
}

export default function EngineConsole({ className = "" }: Props) {
  const [kbs, setKbs] = useState<EngineKB[]>([]);
  const [packs, setPacks] = useState<EnginePack[]>([]);
  const [packKey, setPackKey] = useState("");
  const [selected, setSelected] = useState("");
  const [config, setConfig] = useState<EngineConfigPayload | null>(null);
  const [build, setBuild] = useState<EngineBuild | null>(null);
  const [preset, setPreset] = useState("balanced@1");
  const [name, setName] = useState("我的知识库");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const loadKbs = useCallback(async () => {
    try {
      const list = await fetchEngineKbs();
      setKbs(list);
      setSelected((current) => current || list[0]?.kbId || "");
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载知识库失败");
    }
  }, []);

  const loadKb = useCallback(async (kbId: string) => {
    if (!kbId) return;
    try {
      const data = await fetchEngineConfig(kbId);
      setConfig(data);
      setPreset(String(data.config.preset ?? "balanced@1"));
      const active = kbs.find((item) => item.kbId === kbId)?.activeBuildId;
      if (active) {
        setBuild(await fetchEngineBuild(active));
      } else {
        setBuild(null);
      }
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载配置失败");
    }
  }, [kbs]);

  useEffect(() => {
    void loadKbs();
    void fetchEnginePacks()
      .then((data) => {
        setPacks(data.installed);
        setPackKey(
          (current) =>
            current ||
            (data.installed[0]
              ? `${data.installed[0].id}@${data.installed[0].version}`
              : ""),
        );
      })
      .catch(() => setPacks([]));
  }, [loadKbs]);

  useEffect(() => {
    if (selected) void loadKb(selected);
  }, [selected, loadKb]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };

  const handleCreateKb = () =>
    run(async () => {
      const kb = await createEngineKb(name.trim() || "我的知识库");
      await loadKbs();
      setSelected(kb.kbId);
      setNotice(`已创建知识库 ${kb.kbId}`);
    });

  const handleSave = () =>
    run(async () => {
      if (!selected) return;
      const data = await patchEngineConfig(selected, { preset });
      setConfig(data);
      setNotice(
        data.impact?.requiresNewBuild
          ? `配置已保存；需要新 Build（${data.impact.rebuildMode ?? "full"}）`
          : "配置已保存，无需新 Build",
      );
      await loadKbs();
    });

  const handleBindPack = () =>
    run(async () => {
      if (!selected || !packKey) return;
      const [packId, version] = packKey.split("@");
      await bindEnginePack(selected, packId, version);
      await loadKbs();
      setNotice(`已绑定 ${packId}@${version}`);
    });

  const handleBuild = () =>
    run(async () => {
      if (!selected) return;
      const created = await createEngineBuild(selected, preset);
      setBuild(await fetchEngineBuild(created.buildId));
      setNotice(`已创建 Build ${created.buildId}`);
    });

  const handleActivate = () =>
    run(async () => {
      if (!build) return;
      await activateEngineBuild(build.buildId);
      setBuild(await fetchEngineBuild(build.buildId));
      await loadKbs();
      setNotice(`Build ${build.buildId} 已激活`);
    });

  return (
    <section className={`h-full overflow-auto px-4 py-5 md:px-6 ${className}`}>
      <div className="mx-auto max-w-4xl space-y-4">
        <header>
          <div className="ui-kicker">Engine Console · P2</div>
          <h2 className="ui-brand-title mt-1">新引擎配置</h2>
          <p className="mt-1 text-sm opacity-70">
            仅使用 /api/v1 并行入口；旧问答与文档管理保持原状。
          </p>
        </header>

        <div className="rounded-xl border border-slate-500/20 bg-slate-500/5 p-4">
          <div className="text-sm font-medium">知识库</div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <select
              className="ui-btn ui-btn-ghost ui-btn-sm min-w-48"
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
              aria-label="选择知识库"
            >
              {kbs.length === 0 && <option value="">尚未创建知识库</option>}
              {kbs.map((item) => (
                <option key={item.kbId} value={item.kbId}>
                  {item.name}（{item.kbId}）
                </option>
              ))}
            </select>
            <input
              className="ui-btn ui-btn-ghost ui-btn-sm min-w-40"
              value={name}
              onChange={(e) => setName(e.target.value)}
              aria-label="新知识库名称"
            />
            <button
              type="button"
              className="ui-btn ui-btn-primary ui-btn-sm"
              onClick={handleCreateKb}
              disabled={busy}
            >
              创建知识库
            </button>
          </div>
        </div>

        {selected && (
          <div className="rounded-xl border border-slate-500/20 bg-slate-500/5 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="text-sm font-medium">Domain Pack</div>
              <span className="text-xs opacity-60">
                {kbs.find((item) => item.kbId === selected)?.domainPackId
                  ? `已绑定 ${kbs.find((item) => item.kbId === selected)?.domainPackId}@${
                      kbs.find((item) => item.kbId === selected)?.domainPackVersion ?? ""
                    }`
                  : "未绑定"}
              </span>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <select
                className="ui-btn ui-btn-ghost ui-btn-sm min-w-56"
                value={packKey}
                onChange={(e) => setPackKey(e.target.value)}
                aria-label="选择已安装 Pack"
              >
                {packs.length === 0 && <option value="">尚无已安装 Pack</option>}
                {packs.map((pack) => (
                  <option key={`${pack.id}@${pack.version}`} value={`${pack.id}@${pack.version}`}>
                    {pack.id}@{pack.version}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="ui-btn ui-btn-ghost ui-btn-sm"
                onClick={handleBindPack}
                disabled={busy || !packKey}
              >
                绑定到当前知识库
              </button>
            </div>
          </div>
        )}

        {selected && (
          <div className="rounded-xl border border-slate-500/20 bg-slate-500/5 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="text-sm font-medium">检索与构建</div>
              <span className="text-xs opacity-60">
                配置版本 {config?.version ?? 0}
                {config?.etag ? ` · ${config.etag}` : ""}
              </span>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <label className="text-sm" htmlFor="engine-preset">
                检索预设
              </label>
              <select
                id="engine-preset"
                className="ui-btn ui-btn-ghost ui-btn-sm"
                value={preset}
                onChange={(e) => setPreset(e.target.value)}
              >
                <option value="precision@1">precision@1</option>
                <option value="balanced@1">balanced@1</option>
                <option value="recall@1">recall@1</option>
              </select>
              <button
                type="button"
                className="ui-btn ui-btn-ghost ui-btn-sm"
                onClick={handleSave}
                disabled={busy}
              >
                保存配置
              </button>
              <button
                type="button"
                className="ui-btn ui-btn-primary ui-btn-sm"
                onClick={handleBuild}
                disabled={busy}
              >
                创建 Build
              </button>
            </div>
            {config?.impact?.requiresNewBuild && (
              <p className="mt-2 text-xs opacity-70">
                影响：需要新 Build（{config.impact.rebuildMode ?? "full"}）
              </p>
            )}
            <div className="mt-3 rounded-lg border border-slate-500/20 p-3 text-sm">
              {build ? (
                <div className="flex flex-wrap items-center gap-2">
                  <span>Build：{build.buildId}</span>
                  <span className="opacity-70">状态 {build.status}</span>
                  <span className="opacity-70">健康 {build.health}</span>
                  <span className="opacity-70">预设 {build.preset}</span>
                  {build.status !== "active" && (
                    <button
                      type="button"
                      className="ui-btn ui-btn-primary ui-btn-sm"
                      onClick={handleActivate}
                      disabled={busy}
                    >
                      激活
                    </button>
                  )}
                </div>
              ) : (
                <span className="opacity-70">尚无 Build；保存配置后可创建并激活。</span>
              )}
            </div>
          </div>
        )}

        {error && <p className="text-sm text-red-500">{error}</p>}
        {notice && <p className="text-sm text-emerald-500">{notice}</p>}
      </div>
    </section>
  );
}
