#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""md_report.py — 把分析报告 Markdown 渲染成**单文件、离线可开**的 HTML 页面。

只覆盖本项目报告常用的 Markdown 子集（够用且可控，不引第三方依赖）：
  # / ## / ### 标题 · 段落 · --- 分隔线 · > 引用 · 无序/有序列表
  | 表格 |（首行表头 + 分隔行）· **粗体** · `行内代码` · [文字](链接)
表格默认第一列作为行标题；`active` 列为高亮可加 `**...**`。

用法:
  python wxlocal/analyze/md_report.py <in.md> [out.html] [--title 标题] [--sub 副标题]
"""
import argparse, html, os, re, sys, datetime

CSS = """
:root{--bg:#f6f7f9;--panel:#fff;--line:#e6e8ec;--ink:#1b1f24;--ink2:#5b6472;--ink3:#8c95a3;
--accent:#1a56c4;--shadow:0 1px 2px rgba(16,24,40,.06),0 6px 20px rgba(16,24,40,.05)}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.75 "PingFang SC","Microsoft YaHei","Segoe UI",Roboto,system-ui,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:30px 22px 70px}
.doc{background:var(--panel);border:1px solid var(--line);border-radius:14px;
padding:30px 34px 40px;box-shadow:var(--shadow)}
h1{font-size:25px;margin:0 0 6px;letter-spacing:.2px}
h1+blockquote{margin-top:10px}
.meta{color:var(--ink3);font-size:12.5px;margin:0 0 22px;
padding-bottom:14px;border-bottom:1px solid var(--line)}
h2{font-size:19px;margin:38px 0 12px;padding-top:14px;border-top:1px solid var(--line)}
h3{font-size:16.5px;margin:26px 0 10px;color:#14315f}
h4{font-size:14.5px;margin:20px 0 8px;color:var(--ink2)}
p{margin:9px 0}
ul,ol{margin:9px 0;padding-left:22px}
li{margin:4px 0}
hr{border:0;border-top:1px solid var(--line);margin:26px 0}
blockquote{margin:12px 0;padding:10px 14px;background:#f2f6fd;border-left:3px solid #9db8ea;
border-radius:0 8px 8px 0;color:#2c3a52;font-size:14px}
blockquote p{margin:4px 0}
code{background:#eef1f5;padding:1.5px 5px;border-radius:5px;font-size:12.5px;
font-family:ui-monospace,Consolas,"Courier New",monospace;color:#0b4f6c;word-break:break-all}
strong{font-weight:680;color:#0f2f66}
a{color:var(--accent);text-decoration:none;border-bottom:1px solid #c3d4f2}
a:hover{border-bottom-color:var(--accent)}
.tw{overflow-x:auto;margin:14px 0}
table{width:100%;border-collapse:collapse;font-size:13.5px;min-width:520px}
th,td{padding:8px 11px;border-bottom:1px solid #eef1f5;text-align:left;vertical-align:top}
thead th{background:#fbfcfd;font-size:12.5px;color:var(--ink2);font-weight:600;
white-space:nowrap;border-bottom:1px solid var(--line)}
tbody tr:hover{background:#fafbfd}
tbody td:first-child{font-weight:600;color:#14315f}
.foot{color:var(--ink3);font-size:12px;margin-top:18px;text-align:right}
@media(max-width:640px){.doc{padding:20px 16px 28px}.wrap{padding:16px 10px 40px}}
"""


def inline(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', s)
    return s


def split_row(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def is_sep(line):
    return bool(re.match(r"^\|[\s:|-]+\|$", line.strip())) and "-" in line


def render(md):
    lines = md.splitlines()
    out, i = [], 0
    in_ul = in_ol = False

    def close_list():
        nonlocal in_ul, in_ol
        if in_ul: out.append("</ul>"); in_ul = False
        if in_ol: out.append("</ol>"); in_ol = False

    while i < len(lines):
        ln = lines[i]
        st = ln.strip()

        # 表格
        if st.startswith("|") and i + 1 < len(lines) and is_sep(lines[i + 1]):
            close_list()
            head = split_row(st)
            i += 2
            body = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                body.append(split_row(lines[i])); i += 1
            out.append('<div class="tw"><table><thead><tr>'
                       + "".join(f"<th>{inline(h)}</th>" for h in head)
                       + "</tr></thead><tbody>")
            for r in body:
                r += [""] * (len(head) - len(r))
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r[:len(head)]) + "</tr>")
            out.append("</tbody></table></div>")
            continue

        if st == "---":
            close_list(); out.append("<hr>"); i += 1; continue
        m = re.match(r"^(#{1,4})\s+(.*)$", st)
        if m:
            close_list()
            lv = len(m.group(1))
            out.append(f"<h{lv}>{inline(m.group(2))}</h{lv}>")
            i += 1; continue
        if st.startswith(">"):
            close_list()
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip()); i += 1
            out.append("<blockquote>" + "".join(f"<p>{inline(b)}</p>" for b in buf if b) + "</blockquote>")
            continue
        if re.match(r"^[-*]\s+", st):
            if not in_ul: close_list(); out.append("<ul>"); in_ul = True
            out.append(f"<li>{inline(re.sub(r'^[-*]\\s+', '', st))}</li>"); i += 1; continue
        if re.match(r"^\d+\.\s+", st):
            if not in_ol: close_list(); out.append("<ol>"); in_ol = True
            out.append(f"<li>{inline(re.sub(r'^\\d+\\.\\s+', '', st))}</li>"); i += 1; continue
        if not st:
            close_list(); i += 1; continue
        close_list()
        out.append(f"<p>{inline(st)}</p>")
        i += 1
    close_list()
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("md")
    ap.add_argument("out", nargs="?", default=None)
    ap.add_argument("--title", default=None)
    ap.add_argument("--sub", default=None)
    a = ap.parse_args()
    src = open(a.md, encoding="utf-8").read()
    m = re.search(r"^#\s+(.+)$", src, re.M)
    title = a.title or (m.group(1) if m else os.path.basename(a.md))
    out_path = a.out or os.path.splitext(a.md)[0] + ".html"
    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>{CSS}</style></head>
<body><div class="wrap"><div class="doc">
{render(src)}
<div class="foot">生成于 {datetime.datetime.now():%Y-%m-%d %H:%M} · 由 md_report.py 渲染（单文件离线可开）</div>
</div></div></body></html>
"""
    open(out_path, "w", encoding="utf-8").write(html_doc)
    print(f"✓ {out_path}  ({len(html_doc):,} 字符)")


if __name__ == "__main__":
    main()
