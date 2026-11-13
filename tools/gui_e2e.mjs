// 画面の操作の試験: Edge / Chrome（画面なし）を DevTools プロトコルで動かす（Node 22 以上）。
//   python tools/gui_serve_fake.py <作業フォルダ> 120   # 1 行目の URL を使う
//   node tools/gui_e2e.mjs <URL> <作業フォルダ> <スクリーンショット.png>
// ブラウザの場所は環境変数 BROWSER で変えられる（既定: Windows は Edge、ほかは chromium）。
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const [url, ws, shot] = process.argv.slice(2);
const EDGE = process.env.BROWSER || (process.platform === "win32" ? "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" : "chromium");
const port = 9333;
const prof = path.join(path.dirname(shot), "edgeprof-e2e");
const edge = spawn(EDGE, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${port}`, `--user-data-dir=${prof}`,
  "--window-size=1500,1400", "--autoplay-policy=no-user-gesture-required", "about:blank"], { stdio: "ignore" });
const sleep = ms => new Promise(r => setTimeout(r, ms));

let target = null;
for (let i = 0; i < 50 && !target; i++) {
  await sleep(200);
  try { const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); target = list.find(t => t.type === "page"); } catch { /* 起動中 */ }
}
if (!target) { console.log("Edge につながらない"); edge.kill(); process.exit(1); }
const sock = new WebSocket(target.webSocketDebuggerUrl);
await new Promise(r => sock.addEventListener("open", r));
let mid = 0; const pend = new Map(); const errors = [];
sock.addEventListener("message", ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); return; }
  if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
  if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") errors.push("console.error: " + m.params.args.map(a => a.value ?? a.description).join(" "));
});
const cdp = (method, params = {}) => new Promise(r => { const id = ++mid; pend.set(id, r); sock.send(JSON.stringify({ id, method, params })); });
const ev = async expr => {
  const r = await cdp("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true });
  if (r.result.exceptionDetails) return "EXC: " + (r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text);
  return r.result.result.value;
};
await cdp("Runtime.enable"); await cdp("Page.enable");
await cdp("Page.navigate", { url });
await sleep(2500);

const results = [];
const check = (name, ok, detail) => { results.push({ name, ok: !!ok, detail }); console.log(`${ok ? "OK " : "NG "} ${name}${detail !== undefined ? "  " + JSON.stringify(detail) : ""}`); };
const q = s => `document.querySelector(${JSON.stringify(s)})`;

let v = await ev(`return { lines: document.querySelectorAll(".lcard").length, chips: [...document.querySelectorAll(".lcard .chip")].map(c => c.textContent), check: ${q("#checkChip")}.textContent, kfs: document.querySelectorAll("#tl .kf").length };`);
check("読み込み: 台詞 2 行・合成済み", v.lines === 2 && v.chips.every(c => c === "合成済み"), v);

v = await ev(`${q("#addLineBtn")}.click(); await new Promise(r => setTimeout(r, 100));
  const ta = document.querySelector("#say-L3"); ta.value = "テストの台詞です。"; ta.dispatchEvent(new Event("change"));
  await new Promise(r => setTimeout(r, 1500));
  const card = document.querySelector('.lcard[data-line="L3"]'); return { chip: card && card.querySelector(".chip").textContent, state: ${q("#saveState")}.textContent };`);
check("台詞を追加して合成", v.chip === "合成済み" && v.state === "未保存の変更あり", v);

v = await ev(`${q("#playBtn")}.click(); await new Promise(r => setTimeout(r, 1300)); ${q("#playBtn")}.click();
  const before = document.querySelectorAll("#tl .kf").length; ${q("#gestSel")}.value = "nod"; ${q("#insertBtn")}.click();
  await new Promise(r => setTimeout(r, 100)); return { before, after: document.querySelectorAll("#tl .kf").length, toast: ${q("#toast")}.textContent };`);
check("ひな形 nod を再生位置に挿入", v.after === v.before + 4, v);

v = await ev(`const lane = document.querySelector('#tl rect.lane-bg[data-lane="waist_yaw"]'); const r = lane.getBoundingClientRect();
  const before = document.querySelectorAll("#tl .kf").length;
  lane.dispatchEvent(new MouseEvent("dblclick", { bubbles: true, clientX: r.left + 300, clientY: r.top + 20 }));
  await new Promise(r => setTimeout(r, 100));
  const after = document.querySelectorAll("#tl .kf").length; const insp = ${q("#insp")}.textContent.includes("腰ヨー");
  ${q("#undoBtn")}.click(); await new Promise(r => setTimeout(r, 100));
  return { before, after, insp, undone: document.querySelectorAll("#tl .kf").length };`);
check("ダブルクリックで足して、元に戻す", v.after === v.before + 1 && v.insp && v.undone === v.before, v);

v = await ev(`${q("#saveBtn")}.click(); await new Promise(r => setTimeout(r, 1200)); return ${q("#saveState")}.textContent;`);
const saved = fs.readFileSync(path.join(ws, "scripts", "demo.yaml"), "utf-8");
check("保存（ファイルに L3 と nod が入る）", v === "保存済み" && saved.includes("テストの台詞です。") && saved.includes("group: nod-"), { state: v });

v = await ev(`document.querySelector('#blockSeg button[data-bi="1"]').click(); await new Promise(r => setTimeout(r, 200));
  return { title: ${q("#linesTitle")}.textContent, empty: !!document.querySelector("#lines .empty"), start: ${q("#bStart")}.value };`);
check("ブロック B2 に切り替え（動作だけ・操作者の合図）", v.title.includes("B2") && v.empty && v.start === "operator", v);

v = await ev(`${q("#envBtn")}.click(); await new Promise(r => setTimeout(r, 1500));
  const items = [...document.querySelectorAll("#docList li")].map(li => li.textContent); document.querySelector("#envDlg [data-close]").click(); return items;`);
check("環境（doctor）", Array.isArray(v) && v.some(t => t.includes("作業フォルダ")), v.length);

v = await ev(`${q("#runMode")}.value = "dry"; ${q("#runBtn")}.click();
  let sawPrompt = false;
  for (let i = 0; i < 80; i++) { await new Promise(r => setTimeout(r, 250));
    if (!${q("#opPrompt")}.hidden) { sawPrompt = true; ${q("#opGo")}.click(); }
    if (${q("#runLog")}.textContent.includes("結果:")) break; }
  return { sawPrompt, log: ${q("#runLog")}.textContent.split("\\n").filter(Boolean).slice(-3), rec: document.querySelectorAll("#recBody tr[data-run]").length };`);
check("空運転（操作者の合図つき）", v.sawPrompt && v.log.some(l => l.includes("結果: ok")) && v.rec >= 1, v);

v = await ev(`document.querySelector('#blockSeg button[data-bi="0"]').click(); await new Promise(r => setTimeout(r, 200));
  ${q("#playBtn")}.click(); await new Promise(r => setTimeout(r, 1200)); const t = parseFloat(${q("#timeLbl")}.textContent);
  ${q("#playBtn")}.click(); return { t, btn: ${q("#playBtn")}.textContent };`);
check("再生（再生位置が進む）", v.t > 0.5 && v.btn.includes("再生"), v);

const shotRes = await cdp("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
fs.writeFileSync(shot, Buffer.from(shotRes.result.data, "base64"));
check("JavaScript のエラーなし", errors.length === 0, errors.slice(0, 5));
sock.close(); edge.kill();
const ng = results.filter(r => !r.ok).length;
console.log(ng ? `NG ${ng} 件` : "すべて OK");
process.exit(ng ? 1 : 0);
