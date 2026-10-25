import { useEffect, useState } from "react";
import { api } from "../api";
import type { Config } from "../types";

/** 设置抽屉: 全部运行参数 (写入后端并持久化) + 登录管理 */
export function SettingsDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [cfg, setCfg] = useState<Config | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (open) api.config().then(setCfg);
  }, [open]);

  if (!open) return null;
  const patch = (p: Partial<Config>) => cfg && setCfg({ ...cfg, ...p });

  const save = async () => {
    if (!cfg) return;
    setSaving(true);
    try {
      await api.setConfig(cfg);
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-slate-900/20 backdrop-blur-sm" />
      <div className="relative w-[440px] max-w-[92vw] h-full bg-white border-l border-ink-700
        p-6 overflow-auto shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-lg font-semibold text-slate-900">运行设置</h2>
          <button className="btn-ghost !px-3 !py-1.5 !text-xs" onClick={onClose}>关闭</button>
        </div>

        {cfg && (
          <div className="space-y-7">
            <Section title="并发与节奏" desc="多 Tab 并行消灭渲染等待; AIMD 自动匹配服务器节奏">
              <Slider label="并发 Tab 数" hint="1-6, 越大越快; 被限流时调低"
                value={cfg.tabs} min={1} max={6} step={1}
                onChange={(v) => patch({ tabs: v })} />
              <NumField label="基础导航间隔 (秒)" hint="同 Tab 导航最小间隔, 叠加 ±30% 抖动"
                value={cfg.min_interval} step={0.1} min={0.3} max={30}
                onChange={(v) => patch({ min_interval: v })} />
              <NumField label="退避上限 (秒)" hint="被限流时 AIMD 最大退避到的间隔"
                value={cfg.ceiling} step={1} min={5} max={300}
                onChange={(v) => patch({ ceiling: v })} />
              <Toggle label="自适应节流 (AIMD)" hint="成功缓慢提速 / 429 立即降速, 推荐开启"
                value={cfg.adaptive} onChange={(v) => patch({ adaptive: v })} />
            </Section>

            <Section title="超时与重试" desc="慢页面兜底与失败自动重试">
              <NumField label="导航超时 (秒)" hint="单页加载超时, 超时按失败计数触发退避"
                value={cfg.nav_timeout} step={1} min={10} max={180}
                onChange={(v) => patch({ nav_timeout: v })} />
              <NumField label="渲染等待上限 (秒)" hint="条件等待内容区稳定的最长时间"
                value={cfg.render_timeout} step={1} min={5} max={60}
                onChange={(v) => patch({ render_timeout: v })} />
              <NumField label="提取重试次数" hint="文档页过短/异常时重新导航重试"
                value={cfg.extract_retries} step={1} min={0} max={3}
                onChange={(v) => patch({ extract_retries: v })} />
              <NumField label="图片并发数" hint="单页图片并行下载协程数"
                value={cfg.image_concurrency} step={1} min={1} max={8}
                onChange={(v) => patch({ image_concurrency: v })} />
            </Section>

            <Section title="内容类型" desc="飞书专有格式的处理策略">
              <Field label="无法提取的类型" hint="表格/幻灯/画板等飞书专有格式">
                <div className="flex gap-2">
                  {([["placeholder", "生成占位说明"], ["skip", "直接跳过"]] as const).map(([v, t]) => (
                    <button key={v}
                      className={`btn ${cfg.unsupported_policy === v ? "bg-lark/10 text-lark-soft border border-lark/50"
                        : "btn-ghost"}`}
                      onClick={() => patch({ unsupported_policy: v })}>
                      {t}
                    </button>
                  ))}
                </div>
              </Field>
              <Toggle label="多维表格导出全部视图" hint="默认仅当前视图; 开启后每个视图一张表, 更慢更全"
                value={cfg.bitable_all_views} onChange={(v) => patch({ bitable_all_views: v })} />
              <Toggle label="视频输出地址文本" hint="页面内视频无法内联, 输出其地址备查"
                value={cfg.video_as_link} onChange={(v) => patch({ video_as_link: v })} />
            </Section>

            <Section title="导出与输出">
              <Field label="导出格式">
                <div className="flex gap-2">
                  {(["md", "pdf"] as const).map((f) => (
                    <button key={f}
                      className={`btn ${cfg.export_format === f ? "bg-lark/10 text-lark-soft border border-lark/50"
                        : "btn-ghost"}`}
                      onClick={() => patch({ export_format: f })}>
                      {f === "md" ? "Markdown" : "合并 PDF"}
                    </button>
                  ))}
                </div>
              </Field>
              <NumField label="PDF 图片宽度 (像素)" hint="降采样转 JPEG 控制体积; 越小 PDF 越小"
                value={cfg.pdf_image_width} step={100} min={600} max={3000}
                onChange={(v) => patch({ pdf_image_width: v })} />
              <Toggle label="自包含导出 (图片 base64 内嵌)" hint="在 selfcontained/ 生成副本, 原件保持轻量"
                value={cfg.embed_images} onChange={(v) => patch({ embed_images: v })} />
              <Toggle label="真实下载附件/文件节点" hint="关闭则仅输出下载链接"
                value={cfg.download_attachments} onChange={(v) => patch({ download_attachments: v })} />
              <Toggle label="启动前清空输出目录" hint="默认关闭 = 增量保留已有内容"
                value={cfg.clean} onChange={(v) => patch({ clean: v })} />
              <Field label="输出目录" hint="留空 = 默认 data/outputs">
                <input className="input font-mono !text-xs" value={cfg.output_dir}
                  placeholder="D:/docs/feishu"
                  onChange={(e) => patch({ output_dir: e.target.value })} />
              </Field>
            </Section>

            <button className="btn-primary w-full" onClick={save} disabled={saving}>
              {saving ? "保存中…" : saved ? "✓ 已保存" : "保存设置"}
            </button>

            <Section title="账号">
              <button className="btn-danger w-full"
                onClick={async () => {
                  if (confirm("清除登录态? 下次采撷需重新扫码。")) await api.loginClear();
                }}>
                清除飞书登录状态 (切换账号)
              </button>
            </Section>
          </div>
        )}
      </div>
    </div>
  );
}

function Section({ title, desc, children }: {
  title: string; desc?: string; children: React.ReactNode;
}) {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
        {desc && <p className="text-[11px] text-slate-400 mt-0.5">{desc}</p>}
      </div>
      {children}
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-sm text-slate-700 mb-1.5">{label}</div>
      {children}
      {hint && <div className="text-[11px] text-slate-400 mt-1">{hint}</div>}
    </div>
  );
}

function NumField({ label, hint, value, step, min, max, onChange }: {
  label: string; hint?: string; value: number;
  step: number; min: number; max: number; onChange: (v: number) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div className="min-w-0">
        <div className="text-sm text-slate-700">{label}</div>
        {hint && <div className="text-[11px] text-slate-400 truncate">{hint}</div>}
      </div>
      <input type="number" step={step} min={min} max={max} value={value}
        onChange={(e) => onChange(+e.target.value)}
        className="input !w-24 !px-2.5 !py-1.5 font-mono text-right !text-sm shrink-0" />
    </div>
  );
}

function Slider({ label, hint, value, min, max, step, onChange }: {
  label: string; hint?: string; value: number;
  min: number; max: number; step: number; onChange: (v: number) => void;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1.5">
        <span className="text-sm text-slate-700">{label}</span>
        <span className="font-mono text-lark-soft text-sm">{value}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(+e.target.value)} className="w-full accent-lark" />
      {hint && <div className="text-[11px] text-slate-400 mt-0.5">{hint}</div>}
    </div>
  );
}

function Toggle({ label, hint, value, onChange }: {
  label: string; hint?: string; value: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div className="min-w-0">
        <div className="text-sm text-slate-700">{label}</div>
        {hint && <div className="text-[11px] text-slate-400">{hint}</div>}
      </div>
      <button role="switch" aria-checked={value}
        className={`w-11 h-6 rounded-full transition-colors shrink-0 relative
          ${value ? "bg-lark" : "bg-slate-300"}`}
        onClick={() => onChange(!value)}>
        <span className={`absolute top-0.5 size-5 rounded-full bg-white shadow transition-all
          ${value ? "left-[22px]" : "left-0.5"}`} />
      </button>
    </div>
  );
}
