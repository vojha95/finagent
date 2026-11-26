// Build a Word version of a FinAgent Markdown document (written for docs/blueprint.md): a title
// page from the lines before the first heading, then headings, paragraphs, lists, tables with
// repeated header rows, block quotes, figures (<img src="media/...png">, resolved next to the input
// file), figure notes and "Not built" call-outs; A4, running header and page numbers.
//
// Needs Node.js and two npm packages, kept outside the repository (no global install):
//   npm install --prefix /tmp/md2docx docx@9.5.1 marked@15.0.12
//   NODE_PATH=/tmp/md2docx/node_modules node scripts/md2docx.js docs/blueprint.md \
//     docs/FinAgent_Blueprint_v3.docx "FinAgent · Project Blueprint · Version 3 (as built), 26 November 2026"
// Documentation tooling only: nothing in finagent/ uses it.
const fs = require("fs");
const path = require("path");
const { marked } = require("marked");
const d = require("docx");

const [, , input, output, headerText = ""] = process.argv;
const baseDir = path.dirname(path.resolve(input));
const src = fs.readFileSync(input, "utf8");

// A4 portrait, 2 cm margins: text width 11906 - 2 * 1134 = 9638 DXA (about 6.69 in).
const PAGE = { width: 11906, height: 16838 };
const MARGIN = 1134;
const TEXT_DXA = PAGE.width - 2 * MARGIN;
const TEXT_PX = Math.floor((TEXT_DXA / 1440) * 96);
const FONT = "Arial";
const MONO = "Courier New";
const BLUE = "1F3864";

const decode = (s) => String(s)
  .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, "\"")
  .replace(/&#39;/g, "'").replace(/&amp;/g, "&");

// ---------- inline ----------
function runs(tokens, fmt = {}, out = []) {
  for (const t of tokens || []) {
    switch (t.type) {
      case "strong": runs(t.tokens, { ...fmt, bold: true }, out); break;
      case "em": runs(t.tokens, { ...fmt, italics: true }, out); break;
      case "del": runs(t.tokens, { ...fmt, strike: true }, out); break;
      case "codespan":
        out.push(new d.TextRun({ text: decode(t.text), font: MONO, size: fmt.size ? fmt.size - 1 : 20,
          bold: fmt.bold, italics: fmt.italics, color: fmt.color })); break;
      case "link": {
        const label = runs(t.tokens, { ...fmt, color: "1F4E79", underline: {} }, []);
        if (/^https?:/.test(t.href)) out.push(new d.ExternalHyperlink({ link: t.href, children: label }));
        else out.push(...label);
        break;
      }
      case "br": out.push(new d.TextRun({ break: 1 })); break;
      case "html": {
        if (/^<br\s*\/?>$/i.test(t.text.trim())) out.push(new d.TextRun({ break: 1 }));
        break; // other inline html (comments) is dropped
      }
      case "image": break;
      case "text":
        if (t.tokens && t.tokens.length) { runs(t.tokens, fmt, out); break; }
      // falls through
      case "escape":
      default:
        if (t.text !== undefined) {
          out.push(new d.TextRun({ text: decode(t.text), bold: fmt.bold, italics: fmt.italics,
            strike: fmt.strike, font: fmt.font, size: fmt.size, color: fmt.color,
            underline: fmt.underline }));
        }
    }
  }
  return out;
}

const plain = (tokens) => (tokens || []).map((t) => (t.tokens ? plain(t.tokens) : decode(t.text || ""))).join("");

// ---------- images ----------
function pngSize(file) {
  const b = fs.readFileSync(file);
  return { w: b.readUInt32BE(16), h: b.readUInt32BE(20), data: b };
}

function imageParagraph(html) {
  const m = /src="([^"]+)"/.exec(html);
  if (!m) return null;
  const file = path.join(baseDir, m[1]);
  if (!fs.existsSync(file)) return new d.Paragraph({ children: [new d.TextRun({ text: `[missing figure ${m[1]}]`, italics: true })] });
  const { w, h, data } = pngSize(file);
  const width = Math.min(TEXT_PX, w);
  const height = Math.round((width * h) / w);
  const maxH = 820; // keep a figure on one page
  const scale = height > maxH ? maxH / height : 1;
  return new d.Paragraph({
    alignment: d.AlignmentType.CENTER, keepNext: true, spacing: { before: 120, after: 60 },
    children: [new d.ImageRun({ type: "png", data, transformation: { width: Math.round(width * scale), height: Math.round(height * scale) },
      altText: { title: "Figure", description: path.basename(file), name: path.basename(file) } })],
  });
}

// ---------- tables ----------
function columnWidths(table) {
  const n = table.header.length;
  const len = (cell) => Math.min(70, Math.max(5, plain(cell.tokens).length));
  const weights = Array.from({ length: n }, (_, i) => {
    const cells = [table.header[i], ...table.rows.map((r) => r[i] || { tokens: [] })];
    const avg = cells.reduce((s, c) => s + len(c), 0) / cells.length;
    const max = Math.max(...cells.map(len));
    return Math.sqrt(0.6 * avg + 0.4 * max);
  });
  const sum = weights.reduce((a, b) => a + b, 0);
  const widths = weights.map((w) => Math.max(700, Math.floor((w / sum) * TEXT_DXA)));
  const scale = TEXT_DXA / widths.reduce((a, b) => a + b, 0);
  const out = widths.map((w) => Math.floor(w * scale));
  out[out.length - 1] += TEXT_DXA - out.reduce((a, b) => a + b, 0);
  return out;
}

function tableBlock(t) {
  const widths = columnWidths(t);
  const size = t.header.length >= 6 ? 15 : 17; // 7.5 pt or 8.5 pt
  const border = { style: d.BorderStyle.SINGLE, size: 4, color: "A6A6A6" };
  const borders = { top: border, bottom: border, left: border, right: border };
  const cell = (c, i, head) => new d.TableCell({
    width: { size: widths[i], type: d.WidthType.DXA }, borders,
    margins: { top: 50, bottom: 50, left: 80, right: 80 },
    shading: head ? { type: d.ShadingType.CLEAR, color: "auto", fill: "D9E2F3" } : undefined,
    children: [new d.Paragraph({ spacing: { before: 0, after: 0, line: 240 },
      children: runs(c.tokens, { size, bold: head || undefined }) })],
  });
  const noHeader = t.header.every((c) => !plain(c.tokens).trim());
  const rows = noHeader ? [] : [new d.TableRow({ tableHeader: true, cantSplit: true, children: t.header.map((c, i) => cell(c, i, true)) })];
  for (const r of t.rows) rows.push(new d.TableRow({ cantSplit: true, children: t.header.map((_, i) => noHeader && i === 0
    ? new d.TableCell({ width: { size: widths[0], type: d.WidthType.DXA }, borders, margins: { top: 50, bottom: 50, left: 80, right: 80 },
        shading: { type: d.ShadingType.CLEAR, color: "auto", fill: "EEF2F8" },
        children: [new d.Paragraph({ spacing: { before: 0, after: 0, line: 240 }, children: runs((r[0] || { tokens: [] }).tokens, { size, bold: true }) })] })
    : cell(r[i] || { tokens: [] }, i, false)) }));
  return [new d.Table({ width: { size: TEXT_DXA, type: d.WidthType.DXA }, columnWidths: widths, rows }),
    new d.Paragraph({ spacing: { before: 0, after: 120 }, children: [] })];
}

// ---------- blocks ----------
let olInstance = 0;
function listBlock(list, level, out) {
  const instance = list.ordered ? ++olInstance : 0;
  for (const item of list.items) {
    let first = true;
    for (const t of item.tokens) {
      if (t.type === "list") { listBlock(t, Math.min(level + 1, 2), out); continue; }
      const inl = t.tokens ? runs(t.tokens) : runs([t]);
      if (!inl.length) continue;
      out.push(new d.Paragraph({
        numbering: first ? { reference: list.ordered ? "ol" : "ul", level, instance: list.ordered ? instance : undefined } : undefined,
        indent: first ? undefined : { left: 720 * (level + 1) },
        spacing: { before: 0, after: 80 }, children: inl,
      }));
      first = false;
    }
  }
}

function blocks(tokens, ctx = {}) {
  const out = [];
  for (const t of tokens) {
    switch (t.type) {
      case "heading": {
        const level = [d.HeadingLevel.HEADING_1, d.HeadingLevel.HEADING_2, d.HeadingLevel.HEADING_3, d.HeadingLevel.HEADING_4][Math.min(t.depth, 4) - 1];
        const txt = plain(t.tokens);
        const brk = t.depth === 1 && !ctx.noBreak && /^(1\.\s|Appendix A\b)/.test(txt);
        out.push(new d.Paragraph({ heading: level, pageBreakBefore: brk,
          border: t.depth === 1 && !brk ? { top: { style: d.BorderStyle.SINGLE, size: 6, color: "8EAADB", space: 6 } } : undefined,
          spacing: t.depth === 1 && !brk ? { before: 480, after: 160 } : undefined,
          children: runs(t.tokens) }));
        break;
      }
      case "paragraph": {
        const only = t.tokens.length === 1 && t.tokens[0].type === "html" && /<img/i.test(t.tokens[0].text);
        const html = t.tokens.filter((x) => x.type === "html").map((x) => x.text).join(" ");
        if (only || (/^<img/i.test(t.text.trim()))) { const p = imageParagraph(t.text); if (p) out.push(p); break; }
        if (/<img/i.test(html)) { const p = imageParagraph(html); if (p) out.push(p); }
        const text = t.text.trim();
        if (/^\*\*Not built\*\*/.test(text)) {
          out.push(new d.Paragraph({ indent: { left: 120 }, spacing: { before: 100, after: 100 },
            border: { left: { style: d.BorderStyle.SINGLE, size: 18, color: "C55A11", space: 8 } },
            shading: { type: d.ShadingType.CLEAR, color: "auto", fill: "FBEFE6" }, children: runs(t.tokens) }));
          break;
        }
        const caption = /^\*(Figure|Figure note)/.test(text);
        out.push(new d.Paragraph({
          alignment: caption ? d.AlignmentType.CENTER : undefined,
          spacing: caption ? { before: 0, after: 200 } : undefined,
          children: runs(t.tokens, caption ? { size: 19, color: "404040" } : {}),
        }));
        break;
      }
      case "html":
        if (/<img/i.test(t.text)) { const p = imageParagraph(t.text); if (p) out.push(p); }
        break;
      case "list": listBlock(t, 0, out); break;
      case "table": out.push(...tableBlock(t)); break;
      case "blockquote":  // paragraphs get an indent, a left rule and a light fill
        for (const inner of t.tokens) {
          if (inner.type !== "paragraph") { out.push(...blocks([inner], { noBreak: true })); continue; }
          out.push(new d.Paragraph({ indent: { left: 360 }, spacing: { before: 60, after: 60 },
            border: { left: { style: d.BorderStyle.SINGLE, size: 18, color: "8EAADB", space: 8 } },
            shading: { type: d.ShadingType.CLEAR, color: "auto", fill: "F3F6FB" },
            children: runs(inner.tokens) }));
        }
        break;
      case "code":
        for (const line of t.text.split("\n")) {
          out.push(new d.Paragraph({ spacing: { before: 0, after: 0 }, shading: { type: d.ShadingType.CLEAR, color: "auto", fill: "F2F2F2" },
            children: [new d.TextRun({ text: line || " ", font: MONO, size: 18 })] }));
        }
        out.push(new d.Paragraph({ children: [] }));
        break;
      case "hr":
        out.push(new d.Paragraph({ border: { bottom: { style: d.BorderStyle.SINGLE, size: 6, color: "A6A6A6", space: 1 } }, children: [] }));
        break;
      case "space": break;
      default:
        if (t.text) out.push(new d.Paragraph({ children: [new d.TextRun(decode(t.text))] }));
    }
  }
  return out;
}

// ---------- title page ----------
const lexed = marked.lexer(src);
const firstH = lexed.findIndex((t) => t.type === "heading");
const titleTokens = lexed.slice(0, firstH).filter((t) => t.type === "paragraph");
const body = lexed.slice(firstH);
const title = [];
const titleAt = Math.max(0, titleTokens.findIndex((t) => t.tokens.length === 1 && t.tokens[0].type === "strong"));
titleTokens.forEach((t, i) => {
  const big = i === titleAt, sub = i === titleAt + 1;
  title.push(new d.Paragraph({
    alignment: d.AlignmentType.CENTER,
    spacing: { before: big ? (titleAt ? 1600 : 2400) : sub ? 240 : 160, after: big ? 120 : 160 },
    children: runs(t.tokens, big ? { size: 64, bold: true, color: BLUE } : sub ? { size: 32, color: BLUE }
      : i < titleAt ? { size: 18, italics: true, color: "7F7F7F" } : { size: 22 }),
  }));
});

const doc = new d.Document({
  creator: "FinAgent",
  title: headerText || "FinAgent blueprint",
  description: "FinAgent project blueprint",
  styles: {
    default: { document: { run: { font: FONT, size: 22 }, paragraph: { spacing: { after: 120, line: 276 } } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: FONT, size: 34, bold: true, color: BLUE }, paragraph: { spacing: { before: 240, after: 160 }, keepNext: true, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: FONT, size: 27, bold: true, color: BLUE }, paragraph: { spacing: { before: 280, after: 120 }, keepNext: true, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: FONT, size: 24, bold: true, color: "2F5496" }, paragraph: { spacing: { before: 220, after: 100 }, keepNext: true, outlineLevel: 2 } },
      { id: "Heading4", name: "Heading 4", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: FONT, size: 22, bold: true, italics: true, color: "2F5496" }, paragraph: { spacing: { before: 200, after: 80 }, keepNext: true, outlineLevel: 3 } },
    ],
  },
  numbering: {
    config: [
      { reference: "ul", levels: [0, 1, 2].map((l) => ({ level: l, format: d.LevelFormat.BULLET, text: ["•", "–", "◦"][l], alignment: d.AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 360 * (l + 1) + 0, hanging: 260 } } } })) },
      { reference: "ol", levels: [0, 1, 2].map((l) => ({ level: l, format: [d.LevelFormat.DECIMAL, d.LevelFormat.LOWER_LETTER, d.LevelFormat.LOWER_ROMAN][l], text: `%${l + 1}.`, alignment: d.AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 400 * (l + 1), hanging: 300 } } } })) },
    ],
  },
  sections: [
    // a title page only when the document has lines before its first heading (the blueprint does)
    ...(title.length ? [{ properties: { page: { size: PAGE, margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN } } },
      children: title }] : []),
    { properties: { page: { size: PAGE, margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN } } },
      headers: { default: new d.Header({ children: [new d.Paragraph({ alignment: d.AlignmentType.RIGHT,
        border: { bottom: { style: d.BorderStyle.SINGLE, size: 4, color: "BFBFBF", space: 2 } },
        children: [new d.TextRun({ text: headerText, size: 16, color: "7F7F7F" })] })] }) },
      footers: { default: new d.Footer({ children: [new d.Paragraph({ alignment: d.AlignmentType.CENTER,
        children: [new d.TextRun({ text: "Page ", size: 16, color: "7F7F7F" }), new d.TextRun({ children: [d.PageNumber.CURRENT], size: 16, color: "7F7F7F" }),
          new d.TextRun({ text: " of ", size: 16, color: "7F7F7F" }), new d.TextRun({ children: [d.PageNumber.TOTAL_PAGES], size: 16, color: "7F7F7F" })] })] }) },
      children: blocks(body) },
  ],
});

d.Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(output, buf);
  console.log(`wrote ${output}: ${buf.length} bytes; ${body.length} blocks`);
});
