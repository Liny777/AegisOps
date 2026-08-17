import { useState } from "react";
import { color, radius } from "../theme/tokens";
import { Button, Icon, TextInput } from "../ui";
import { api } from "../lib/api";

const MAX_SKILL_ZIP = 50 * 1024 * 1024;  // 29.3 §2.1：ZIP ≤ 50MB（与后端 api/skill_upload.py 同值）

const TRANSPORTS = ["streamable_http", "sse", "jsonrpc"] as const;

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <div style={{ fontSize: 12.5, fontWeight: 700, marginBottom: 7, color: color.textStrong }}>{children}</div>;
}

/**
 * 管理台注册/上传**平台级**资产（29.9，上游一律 is_system=true）。
 *
 * 与设置页 AssetDialog（个人级）的差异：skill 侧补回分类/标签输入（29.9 §2.1 把 category 标为必填，
 * 用户面当初移除了这两项）；mcp 侧字段随上游词汇（server_name 兼作 server_id / server_url / transport）。
 * 两者都需要调用者在 Skill Hub / MCP Registry 侧是超级管理员，否则上游回 1003（我们转 403 并说清）。
 */
export function PlatformAssetDialog({ kind, onClose, onSaved }: {
  kind: "skill" | "mcp";
  onClose: () => void;
  onSaved: (msg: string) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [category, setCategory] = useState("");
  const [tagsText, setTagsText] = useState("");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("https://");
  const [desc, setDesc] = useState("");
  const [version, setVersion] = useState("1.0.0");
  const [transport, setTransport] = useState<string>("streamable_http");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const pickFile = (f: File | null) => {
    setErr("");
    if (f && !/\.zip$/i.test(f.name)) { setErr("请选择 .zip 包"); setFile(null); return; }
    if (f && f.size > MAX_SKILL_ZIP) { setErr("ZIP 超过 50MB 上限"); setFile(null); return; }
    setFile(f);
  };

  const tags = tagsText.split(/[,，、\s]+/).map((t) => t.trim()).filter(Boolean);
  const ok = kind === "skill" ? Boolean(file) : name.trim().length > 0 && url.trim().length > 8;

  const submit = () => {
    if (!ok || busy) return;
    setBusy(true);
    setErr("");
    const req = kind === "skill" && file
      ? api.adminUploadSkill(file, category.trim(), tags).then((r) => {
          // 同名收敛结果显式告知：静默清掉旧行会让管理员以为「怎么少了一条」
          const base = r.action === "version_updated" ? `已更新「${r.display_name}」` : `已上传「${r.display_name}」`;
          return r.merged > 0 ? `${base}，并清理了 ${r.merged} 条同名旧记录` : base;
        })
      : api.adminRegisterMcp({
          server_name: name.trim(), server_url: url.trim(), description: desc.trim(),
          version: version.trim(), category: category.trim(), tags, transport,
        }).then((r) => (r.action === "updated"
          ? `已更新「${r.server_id}」的注册信息（工具标注保持不变）`
          : `已注册「${r.server_id}」`));
    req.then(onSaved).catch((e) => setErr((e as Error).message)).finally(() => setBusy(false));
  };

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 60, background: "rgba(20,24,31,.42)", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 460, maxHeight: "86vh", overflowY: "auto", background: "#fff", borderRadius: radius.modal, padding: "22px 22px 18px" }}>
        <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 14 }}>
          {kind === "skill" ? "上传平台 Skill" : "注册平台 MCP"}
        </div>

        {kind === "skill" ? (
          <>
            <SectionLabel>Skill 包（.zip，含 SKILL.md）</SectionLabel>
            <label style={{ display: "flex", alignItems: "center", gap: 10, border: `1px dashed ${color.borderInput}`, borderRadius: radius.md, padding: "12px 14px", cursor: "pointer", background: color.neutralBg }}>
              <Icon name="upload" size={16} color={color.brand} />
              <span style={{ fontSize: 12.5, color: file ? color.textStrong : color.textSubtle, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {file ? `${file.name}（${(file.size / 1024).toFixed(0)} KB）` : "点击选择 ZIP 文件"}
              </span>
              <input type="file" accept=".zip,application/zip" style={{ display: "none" }}
                onChange={(e) => pickFile(e.target.files?.[0] ?? null)} />
            </label>
            <div style={{ height: 12 }} />
            <SectionLabel>分类</SectionLabel>
            <TextInput value={category} onChange={setCategory} placeholder="如：运维（29.9 建议必填）" />
            <div style={{ height: 12 }} />
            <SectionLabel>标签（选填，逗号分隔）</SectionLabel>
            <TextInput value={tagsText} onChange={setTagsText} placeholder="如：监控,告警" />
          </>
        ) : (
          <>
            <SectionLabel>服务名称（兼作 server_id，须唯一）</SectionLabel>
            <TextInput value={name} onChange={setName} placeholder="如：cmdb-mcp-server" mono />
            <div style={{ height: 12 }} />
            <SectionLabel>Server URL</SectionLabel>
            <TextInput value={url} onChange={setUrl} placeholder="https://cmdb.internal/mcp" mono />
            <div style={{ height: 12 }} />
            <SectionLabel>传输协议</SectionLabel>
            <select value={transport} onChange={(e) => setTransport(e.target.value)}
              style={{ width: "100%", height: 36, border: `1px solid ${color.borderInput}`, borderRadius: radius.md, padding: "0 10px", fontSize: 12.5, background: "#fff", cursor: "pointer", outline: "none" }}>
              {TRANSPORTS.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
            <div style={{ height: 12 }} />
            <SectionLabel>描述（选填）</SectionLabel>
            <TextInput value={desc} onChange={setDesc} placeholder="这个 MCP 提供什么能力" />
            <div style={{ height: 12 }} />
            <div style={{ display: "flex", gap: 10 }}>
              <div style={{ flex: 1 }}>
                <SectionLabel>版本</SectionLabel>
                <TextInput value={version} onChange={setVersion} placeholder="1.0.0" mono />
              </div>
              <div style={{ flex: 1 }}>
                <SectionLabel>分类（选填）</SectionLabel>
                <TextInput value={category} onChange={setCategory} placeholder="如：监控" />
              </div>
            </div>
            <div style={{ height: 12 }} />
            <SectionLabel>标签（选填，逗号分隔）</SectionLabel>
            <TextInput value={tagsText} onChange={setTagsText} placeholder="如：指标,容量" />
          </>
        )}

        <div style={{ marginTop: 12, fontSize: 11.5, color: color.textSubtle, lineHeight: 1.6 }}>
          {kind === "skill"
            ? <>以<b>平台级</b>发布（对全部用户可见）。ZIP 内须含 SKILL.md，其 name 为技能原始名且不得以 system-/user- 开头。若平台已有同名 Skill，上传后会自动收敛为一条记录（旧记录一并清理）。</>
            : <>以<b>平台级</b>注册（对全部用户可见）。同名重新注册为<b>原地更新</b>：仅刷新地址与元信息，已有工具标注与模板绑定保持不变。</>}
          <br />需要在 {kind === "skill" ? "Skill Hub" : "MCP Registry"} 侧具备超级管理员权限，否则会被上游拒绝（403）。
        </div>

        {err ? <div style={{ fontSize: 12, color: color.dangerText, marginTop: 10, lineHeight: 1.6 }}>{err}</div> : null}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 18 }}>
          <Button variant="secondary" onClick={onClose} disabled={busy}>取消</Button>
          <Button icon={busy ? "loader-2" : undefined} disabled={!ok || busy} onClick={submit}>
            {busy ? (kind === "skill" ? "上传中…" : "注册中…") : (kind === "skill" ? "上传" : "注册")}
          </Button>
        </div>
      </div>
    </div>
  );
}
