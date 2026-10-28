#!/usr/bin/env node
/**
 * BlueAI 舆情 ECharts SSR 渲染脚本 v2.0
 *
 * 架构：ECharts 5 SSR → SVG string → @resvg/resvg-js → PNG buffer
 * 优势：零浏览器依赖、零系统库依赖、跨平台一致行为
 *
 * 用法:
 *   node render_chart.js --option "<json>" --output "/tmp/chart.png"
 *   node render_chart.js --file "/tmp/option.json" --output "/tmp/chart.png"
 *   node render_chart.js --js "/tmp/chart.js" --output "/tmp/chart.png"
 *
 * 参数:
 *   --option, -o   ECharts option JSON 字符串（直接传入）
 *   --file, -f     包含 ECharts option 的 JSON 文件路径
 *   --js, -j       包含 option 定义的 JS 文件路径（支持 echarts.graphic.*）
 *   --output, -O   输出 PNG 文件路径 (必需)
 *   --width, -w    图表宽度 (默认: 1200)
 *   --height, -H   图表高度 (默认: 600)
 *   --scale, -s    设备像素比 (默认: 2, 即 2x 高清)
 *   --title, -t    图表标题 (显示在图表上方)
 *   --template     已废弃，保留兼容性（SSR 模式不再使用 HTML 模板）
 *   --chrome       已废弃，保留兼容性（SSR 模式不再使用浏览器）
 */

'use strict';

const echarts = require('echarts');
const { Resvg } = require('@resvg/resvg-js');
const fs = require('fs');
const path = require('path');

// ── 字体加载 ──────────────────────────────────────────────────────────────────

const FONT_DIR = path.join(__dirname, '..', 'assets', 'fonts');

function loadFontFiles() {
  const fonts = [];
  if (fs.existsSync(FONT_DIR)) {
    const files = fs.readdirSync(FONT_DIR).filter(f => /\.(otf|ttf|woff2?)$/i.test(f));
    for (const file of files) {
      fonts.push(path.join(FONT_DIR, file));
    }
  }
  if (fonts.length === 0) {
    throw new Error(`中文字体未找到: ${FONT_DIR}`);
  }
  return fonts;
}

// ── 参数解析 ──────────────────────────────────────────────────────────────────

function parseArgs() {
  const args = process.argv.slice(2);
  const result = {
    option: null,
    file: null,
    html: null,
    js: null,
    output: null,
    width: 1200,
    height: 600,
    scale: 2,
    title: null,
    template: null,
    chrome: null
  };

  for (let i = 0; i < args.length; i++) {
    switch (args[i]) {
      case '--option': case '-o':
        result.option = args[++i]; break;
      case '--file': case '-f':
        result.file = args[++i]; break;
      case '--html': case '-h':
        result.html = args[++i]; break;
      case '--js': case '-j':
        result.js = args[++i]; break;
      case '--output': case '-O':
        result.output = args[++i]; break;
      case '--width': case '-w':
        result.width = parseInt(args[++i], 10); break;
      case '--height': case '-H':
        result.height = parseInt(args[++i], 10); break;
      case '--scale': case '-s':
        result.scale = parseFloat(args[++i]); break;
      case '--title': case '-t':
        result.title = args[++i]; break;
      case '--template':
        result.template = args[++i]; break; // 兼容保留，不使用
      case '--chrome':
        result.chrome = args[++i]; break; // 兼容保留，不使用
    }
  }
  return result;
}

// ── Option 解析（支持多种输入方式）────────────────────────────────────────────

function resolveOption(args) {
  if (args.option) {
    return JSON.parse(args.option);
  }
  if (args.file) {
    const content = fs.readFileSync(args.file, 'utf-8');
    return JSON.parse(content);
  }
  if (args.js) {
    const content = fs.readFileSync(args.js, 'utf-8');
    // 尝试 JSON
    try { return JSON.parse(content); } catch (e) { /* not JSON */ }
    // 用 Function 执行，注入 echarts 以支持 echarts.graphic.LinearGradient 等
    try {
      const fn = new Function('echarts', `
        ${content}
        if (typeof option !== 'undefined') return option;
        throw new Error('no option variable found');
      `);
      return fn(echarts);
    } catch (e) {
      throw new Error(`无法从 JS 文件解析 ECharts option: ${e.message}`);
    }
  }
  if (args.html) {
    // 从 HTML 中提取 option（兼容旧调用方式）
    const html = fs.readFileSync(args.html, 'utf-8');
    const scriptMatch = html.match(/<script[^>]*>([\s\S]*?)<\/script>/gi);
    if (scriptMatch) {
      for (const block of scriptMatch) {
        const code = block.replace(/<\/?script[^>]*>/gi, '');
        if (code.includes('option') && !code.includes('src=')) {
          try {
            const fn = new Function('echarts', `
              ${code}
              if (typeof option !== 'undefined') return option;
            `);
            const result = fn(echarts);
            if (result) return result;
          } catch (e) { /* try next block */ }
        }
      }
    }
    throw new Error('无法从 HTML 文件中提取 ECharts option');
  }
  throw new Error('必须指定 --option, --file, --js 或 --html 参数之一');
}

// ── 标题 SVG 生成 ─────────────────────────────────────────────────────────────

function buildTitleSvg(title, width) {
  if (!title) return { svg: '', height: 0 };
  const titleHeight = 64;
  const svg = `
    <rect x="0" y="0" width="${width}" height="${titleHeight}" fill="#FFFFFF"/>
    <text x="20" y="42" font-family="Source Han Sans SC, PingFang SC, Microsoft YaHei, sans-serif"
          font-size="24" font-weight="500" fill="#1D2129">${escapeXml(title)}</text>
    <line x1="0" y1="${titleHeight - 1}" x2="${width}" y2="${titleHeight - 1}" stroke="#E5E6EB" stroke-width="1"/>
  `;
  return { svg, height: titleHeight };
}

function escapeXml(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&apos;');
}

// ── 辅助：提取 SVG 内容（去掉外层 <svg> 标签）─────────────────────────────────

function extractSvgContent(svgString) {
  let s = svgString.replace(/<\?xml[^?]*\?>\s*/g, '');
  s = s.replace(/^<svg[^>]*>/, '').replace(/<\/svg>\s*$/, '');
  return s;
}

// ── 主渲染流程 ────────────────────────────────────────────────────────────────

function main() {
  const args = parseArgs();

  if (!args.output) {
    console.error('错误: 必须指定 --output 参数');
    process.exit(1);
  }

  if (!args.option && !args.file && !args.js && !args.html) {
    console.error('错误: 必须指定 --option, --file, --js 或 --html 参数之一');
    process.exit(1);
  }

  // 解析 option
  const option = resolveOption(args);

  // 标题处理
  const { svg: titleSvg, height: titleHeight } = buildTitleSvg(args.title, args.width);
  const chartHeight = args.height - titleHeight;

  // ECharts SSR 渲染
  const chart = echarts.init(null, null, {
    renderer: 'svg',
    ssr: true,
    width: args.width,
    height: chartHeight,
  });

  // 强制禁用动画（SSR 不支持）
  option.animation = false;
  option.textStyle = {
    ...(option.textStyle || {}),
    fontFamily: 'Source Han Sans SC',
  };

  chart.setOption(option);
  const chartSvgContent = chart.renderToSVGString();
  chart.dispose();

  // 组合最终 SVG（标题 + 图表）- 物理尺寸 = 逻辑尺寸 × scale
  const physWidth = args.width * args.scale;
  const physHeight = args.height * args.scale;
  const finalSvg = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="${physWidth}" height="${physHeight}" viewBox="0 0 ${args.width} ${args.height}">
  <rect width="100%" height="100%" fill="#FFFFFF"/>
  ${titleSvg}
  <g transform="translate(0, ${titleHeight})">
    ${extractSvgContent(chartSvgContent)}
  </g>
</svg>`;

  // 加载字体
  const fontFiles = loadFontFiles();

  // resvg 渲染 SVG → PNG
  const resvgOpts = {
    font: {
      fontFiles,
      loadSystemFonts: false,
      defaultFontFamily: 'Source Han Sans SC',
    },
    logLevel: 'off',
  };

  const resvg = new Resvg(finalSvg, resvgOpts);
  const pngData = resvg.render();
  const pngBuffer = pngData.asPng();

  // 确保输出目录存在
  const outputDir = path.dirname(args.output);
  if (outputDir && !fs.existsSync(outputDir)) {
    fs.mkdirSync(outputDir, { recursive: true });
  }

  fs.writeFileSync(args.output, pngBuffer);

  const sizeKB = (pngBuffer.length / 1024).toFixed(1);
  console.log(`图表已保存: ${args.output} (${physWidth}×${physHeight}px, ${sizeKB}KB)`);
}

// ── 执行 ──────────────────────────────────────────────────────────────────────

main();
