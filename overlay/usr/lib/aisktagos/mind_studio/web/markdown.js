// Небольшой безопасный Markdown → HTML (без внешних библиотек: окно работает и без интернета).
// Сначала всё экранируется, затем размечается: заголовки, списки, таблицы, цитаты, код с подсветкой.
(function () {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  const KW = /\b(def|class|return|if|elif|else|for|while|in|import|from|as|with|try|except|finally|raise|yield|lambda|async|await|function|const|let|var|new|this|export|default|extends|switch|case|break|continue|public|private|static|void|int|string|bool|true|false|True|False|None|null|undefined|fn|pub|impl|struct|enum|match|use|mod|go|func|package|type|interface|select|from|where|echo|then|fi|do|done)\b/g;

  function highlight(code) {
    // Подсветка по токенам: строки и комментарии вырезаем, чтобы не подсвечивать внутри них
    const out = [];
    const re = /("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|(#[^\n]*|\/\/[^\n]*|\/\*[\s\S]*?\*\/)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][\w]*)(?=\()|([\s\S])/g;
    let m, plain = "";
    const flush = () => { if (plain) { out.push(esc(plain).replace(KW, '<span class="tok-k">$1</span>')); plain = ""; } };
    while ((m = re.exec(code))) {
      if (m[1]) { flush(); out.push('<span class="tok-s">' + esc(m[1]) + "</span>"); }
      else if (m[2]) { flush(); out.push('<span class="tok-c">' + esc(m[2]) + "</span>"); }
      else if (m[3]) { flush(); out.push('<span class="tok-n">' + m[3] + "</span>"); }
      else if (m[4]) { flush(); out.push(KW.test(m[4]) ? esc(m[4]).replace(KW, '<span class="tok-k">$1</span>') : '<span class="tok-f">' + esc(m[4]) + "</span>"); KW.lastIndex = 0; }
      else plain += m[5];
    }
    flush();
    return out.join("");
  }

  function inline(s) {
    // s уже экранирована
    const codes = [];
    s = s.replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return "\u0000" + (codes.length - 1) + "\u0000"; });
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/__([^_]+)__/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
    s = s.replace(/~~([^~]+)~~/g, "<del>$1</del>");
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => "<code>" + codes[+i] + "</code>");
  }

  function render(src) {
    const lines = String(src || "").replace(/\r\n/g, "\n").split("\n");
    const html = [];
    let i = 0;
    while (i < lines.length) {
      let line = lines[i];
      const fence = line.match(/^\s*(```|~~~)\s*([\w+#.-]*)/);
      if (fence) {
        const lang = fence[2] || "";
        const buf = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith(fence[1])) buf.push(lines[i++]);
        i++;
        const code = buf.join("\n");
        html.push('<div class="code"><div class="code-head"><span>' + esc(lang || "код") +
          '</span><button data-copy>Копировать</button></div><pre><code data-raw="' + esc(code) + '">' +
          highlight(code) + "</code></pre></div>");
        continue;
      }
      if (/^\s*$/.test(line)) { i++; continue; }
      const h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) { const n = Math.min(h[1].length, 3); html.push(`<h${n}>${inline(esc(h[2]))}</h${n}>`); i++; continue; }
      if (/^\s*([-*_])\s*\1\s*\1[\s\1]*$/.test(line)) { html.push("<hr>"); i++; continue; }
      if (/^\s*>/.test(line)) {
        const buf = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ""));
        html.push("<blockquote>" + render(buf.join("\n")) + "</blockquote>");
        continue;
      }
      if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
        const row = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(esc(c.trim())));
        const head = row(line);
        i += 2;
        const body = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) body.push(row(lines[i++]));
        html.push("<table><thead><tr>" + head.map((c) => "<th>" + c + "</th>").join("") + "</tr></thead><tbody>" +
          body.map((r) => "<tr>" + r.map((c) => "<td>" + c + "</td>").join("") + "</tr>").join("") + "</tbody></table>");
        continue;
      }
      const li = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
      if (li) {
        const ordered = /\d/.test(li[2]);
        const items = [];
        while (i < lines.length) {
          const m = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
          if (m) { items.push(m[3]); i++; }
          else if (/^\s{2,}\S/.test(lines[i]) && items.length) { items[items.length - 1] += " " + lines[i].trim(); i++; }
          else break;
        }
        const tag = ordered ? "ol" : "ul";
        html.push(`<${tag}>` + items.map((t) => "<li>" + inline(esc(t)) + "</li>").join("") + `</${tag}>`);
        continue;
      }
      const buf = [];
      while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|\s*```|\s*~~~|\s*>|\s*([-*+]|\d+[.)])\s)/.test(lines[i])) buf.push(lines[i++]);
      if (!buf.length) { buf.push(lines[i++]); }
      html.push("<p>" + buf.map((l) => inline(esc(l))).join("<br>") + "</p>");
    }
    return html.join("\n");
  }

  window.MD = { render, esc };
})();
