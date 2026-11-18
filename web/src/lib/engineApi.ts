/** 新引擎 v1 API 封装；旧 /api 链路保持独立。 */

export interface EngineKB {
  kbId: string;
  name: string;
  languageProfile: string;
  domainPackId?: string | null;
  domainPackVersion?: string | null;
  activeBuildId: string | null;
  configVersion: number;
}

export interface EnginePack {
  id: string;
  version: string;
  content_hash?: string;
  status?: string;
  engine_range?: string;
}

export interface ConfigImpact {
  requiresNewBuild: boolean;
  rebuildMode: string | null;
  profiles: string[];
  affected: string[];
  unknown?: string[];
}

export interface EngineConfigPayload {
  config: Record<string, unknown>;
  effective: Record<string, unknown>;
  sources: Record<string, string>;
  version: number;
  etag: string;
  impact?: ConfigImpact;
}

export interface EngineBuild {
  buildId: string;
  kbId: string;
  status: string;
  health: string;
  preset: string;
  createdAt: string;
  activatedAt?: string | null;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json" },
  });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      message = body?.error?.message ?? message;
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

export async function fetchEngineKbs(): Promise<EngineKB[]> {
  const body = await request<{ data: { items: EngineKB[] } }>("/api/v1/kbs");
  return body.data.items ?? [];
}

export async function createEngineKb(name: string): Promise<EngineKB> {
  const kbId = `kb_${Date.now().toString(36)}`;
  const body = await request<{ data: EngineKB }>("/api/v1/kbs", {
    method: "POST",
    body: JSON.stringify({ name, kbId }),
  });
  return body.data;
}

export async function fetchEngineConfig(kbId: string): Promise<EngineConfigPayload> {
  const body = await request<{ data: EngineConfigPayload }>(
    `/api/v1/kbs/${encodeURIComponent(kbId)}/config`,
  );
  return body.data;
}

export async function patchEngineConfig(
  kbId: string,
  patch: Record<string, unknown>,
): Promise<EngineConfigPayload> {
  const body = await request<{ data: EngineConfigPayload }>(
    `/api/v1/kbs/${encodeURIComponent(kbId)}/config`,
    { method: "PATCH", body: JSON.stringify(patch) },
  );
  return body.data;
}

export async function createEngineBuild(
  kbId: string,
  preset: string,
): Promise<{ buildId: string; status: string }> {
  const body = await request<{ data: { buildId: string; status: string } }>(
    `/api/v1/kbs/${encodeURIComponent(kbId)}/builds`,
    { method: "POST", body: JSON.stringify({ preset }) },
  );
  return body.data;
}

export async function fetchEngineBuild(buildId: string): Promise<EngineBuild> {
  const body = await request<{ data: EngineBuild }>(
    `/api/v1/builds/${encodeURIComponent(buildId)}`,
  );
  return body.data;
}

export async function activateEngineBuild(buildId: string): Promise<void> {
  await request(`/api/v1/builds/${encodeURIComponent(buildId)}/activate`, {
    method: "POST",
  });
}

export async function fetchEnginePacks(): Promise<{
  installed: EnginePack[];
  discovered: unknown[];
}> {
  const body = await request<{ data: { installed: EnginePack[]; discovered: unknown[] } }>(
    "/api/v1/packs",
  );
  return body.data;
}

export async function bindEnginePack(
  kbId: string,
  packId: string,
  version: string,
): Promise<void> {
  await request(`/api/v1/kbs/${encodeURIComponent(kbId)}/pack`, {
    method: "POST",
    body: JSON.stringify({ packId, version }),
  });
}
