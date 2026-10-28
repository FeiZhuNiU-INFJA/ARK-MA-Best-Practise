#!/usr/bin/env node
/**
 * Render chart JS (and optionally PNG) from a chart spec or bundle entry.
 *
 * Usage:
 *   node render_from_spec.js --spec /tmp/chart_spec.json --js-out /tmp/chart.js
 *   node render_from_spec.js --spec /tmp/chart_spec.json --output /tmp/chart.png
 *   node render_from_spec.js --bundle /tmp/chart_bundle.json --chart-id chart_1 --output /tmp/chart.png
 */

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const SKILL_ROOT = path.resolve(__dirname, '..');
const DEFAULT_TEMPLATE = path.join(SKILL_ROOT, 'assets/chart_template.html');
const RENDER_SCRIPT = path.join(__dirname, 'render_chart.js');

function parseArgs(argv) {
  const result = {
    spec: null,
    bundle: null,
    chartId: null,
    chartIndex: null,
    jsOut: null,
    output: null,
    width: 1200,
    height: 600,
    template: DEFAULT_TEMPLATE
  };

  for (let i = 0; i < argv.length; i += 1) {
    switch (argv[i]) {
      case '--spec':
      case '-s':
        result.spec = argv[i + 1];
        i += 1;
        break;
      case '--bundle':
      case '-b':
        result.bundle = argv[i + 1];
        i += 1;
        break;
      case '--chart-id':
        result.chartId = argv[i + 1];
        i += 1;
        break;
      case '--chart-index':
        result.chartIndex = Number(argv[i + 1]);
        i += 1;
        break;
      case '--js-out':
        result.jsOut = argv[i + 1];
        i += 1;
        break;
      case '--output':
      case '-o':
        result.output = argv[i + 1];
        i += 1;
        break;
      case '--width':
      case '-w':
        result.width = Number(argv[i + 1]);
        i += 1;
        break;
      case '--height':
      case '-h':
        result.height = Number(argv[i + 1]);
        i += 1;
        break;
      case '--template':
        result.template = argv[i + 1];
        i += 1;
        break;
      default:
        break;
    }
  }

  return result;
}

function fail(message) {
  console.error(`SPEC RENDER FAILED: ${message}`);
  process.exit(1);
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf-8'));
}

function loadSpec(args) {
  if (args.spec) {
    return readJson(path.resolve(args.spec));
  }

  if (!args.bundle) {
    fail('missing --spec <path> or --bundle <path>');
  }

  const bundle = readJson(path.resolve(args.bundle));
  const charts = Array.isArray(bundle) ? bundle : bundle && Array.isArray(bundle.charts) ? bundle.charts : null;
  if (!charts) {
    fail('bundle root must be an array or an object with a charts array');
  }

  if (args.chartId) {
    const chart = charts.find((item) => item && item.chart_id === args.chartId);
    if (!chart) {
      fail(`chart_id not found in bundle: ${args.chartId}`);
    }
    return chart;
  }

  if (Number.isInteger(args.chartIndex)) {
    const chart = charts[args.chartIndex];
    if (!chart) {
      fail(`chart_index out of range: ${args.chartIndex}`);
    }
    return chart;
  }

  if (charts.length === 1) {
    return charts[0];
  }

  fail('bundle contains multiple charts, please provide --chart-id or --chart-index');
}

function getSeriesValues(series) {
  if (Array.isArray(series.values)) {
    return series.values;
  }
  if (Array.isArray(series.data)) {
    return series.data;
  }
  return [];
}

function prelude(width, height) {
  return `
const W = ${width};
const H = ${height};
const BASE_W = 1200;
const BASE_H = 600;
const scale = Math.min(W / BASE_W, H / BASE_H);
const FONT_MAIN = Math.max(13, Math.round(18 * scale));
const FONT_SMALL = Math.max(13, Math.round(14 * scale));
const LEGEND_ICON = Math.max(8, Math.round(10 * scale));

function mixWhite(hex) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return \`rgb(\${Math.round(r * 0.6 + 255 * 0.4)}, \${Math.round(g * 0.6 + 255 * 0.4)}, \${Math.round(b * 0.6 + 255 * 0.4)})\`;
}

function compactCount(value) {
  if (value >= 100000000) return \`\${(value / 100000000).toFixed(1)}亿\`;
  if (value >= 10000) return \`\${(value / 10000).toFixed(1)}万\`;
  return \`\${Math.round(value)}\`;
}

function percentLabel(value) {
  const rounded = Math.round(value * 10) / 10;
  return rounded % 1 === 0 ? \`\${rounded.toFixed(0)}%\` : \`\${rounded.toFixed(1)}%\`;
}
`;
}

function getLabelPolicy(spec, fallbackPolicy) {
  return String(spec.label_policy || fallbackPolicy || 'tooltip_only').trim();
}

function getBarValueFormatter(isPercent) {
  return isPercent ? 'percentLabel(value)' : 'compactCount(value)';
}

function buildTopBarLabelSnippet(policy, isPercent) {
  const formatter = getBarValueFormatter(isPercent);
  return buildTopBarLabelSnippetWithFormatter(policy, formatter);
}

function buildTopBarLabelSnippetWithFormatter(policy, formatter) {
  const fallbackFormatter = formatter || 'compactCount(value)';

  if (policy === 'tooltip_only') {
    return `
      label: { show: false }`;
  }

  if (policy === 'top_key_values' || policy === 'top_key_per_group_value' || policy === 'top_key_per_group_percent') {
    return `
      label: {
        show: true,
        position: "top",
        color: "#4E5969",
        fontSize: FONT_SMALL,
        formatter: ({ value, dataIndex, seriesIndex }) => {
          if (!Array.isArray(seriesData)) {
            return dataIndex < 3 ? ${fallbackFormatter} : "";
          }
          const sameGroupValues = seriesData.map((item) => item.values[dataIndex] || 0);
          const maxValue = Math.max(...sameGroupValues);
          const isKeyBar = value === maxValue || dataIndex === 0;
          return isKeyBar ? ${fallbackFormatter} : "";
        }
      }`;
  }

  return `
      label: {
        show: true,
        position: "top",
        color: "#4E5969",
        fontSize: FONT_SMALL,
        formatter: ({ value }) => ${fallbackFormatter}
      }`;
}

function buildRightBarLabelSnippet(policy) {
  if (policy === 'tooltip_only') {
    return `
      label: { show: false }`;
  }

  if (policy === 'right_key_values') {
    return `
      label: {
        show: true,
        position: "right",
        color: "#4E5969",
        fontSize: FONT_SMALL,
        formatter: ({ value, dataIndex }) => (dataIndex < 5 ? compactCount(value) : "")
      }`;
  }

  return `
      label: {
        show: true,
        position: "right",
        color: "#4E5969",
        fontSize: FONT_SMALL,
        formatter: ({ value }) => compactCount(value)
      }`;
}

function buildStackedLabelSnippet(policy) {
  if (policy === 'tooltip_only') {
    return `
      label: { show: false }`;
  }

  if (policy === 'inside_percent') {
    return `
      label: {
        show: true,
        position: "inside",
        color: "#FFFFFF",
        fontSize: FONT_SMALL,
        formatter: ({ value }) => percentLabel(value)
      }`;
  }

  return `
      label: {
        show: true,
        position: "inside",
        color: "#FFFFFF",
        fontSize: FONT_SMALL,
        formatter: ({ value }) => (value < 8 ? "" : percentLabel(value))
      }`;
}

function buildLineLabelSnippet(policy) {
  if (policy === 'tooltip_only') {
    return `
      label: { show: false }`;
  }

  return `
      label: {
        show: true,
        position: "top",
        distance: 10,
        color: "#4E5969",
        fontSize: FONT_SMALL,
        formatter: (params) => (KEY_INDEXES.has(params.dataIndex) ? compactCount(params.value) : "")
      }`;
}

function buildComboLineLabelSnippet(policy) {
  if (policy !== 'line_key_points_only') {
    return `
      label: { show: false }`;
  }

  return `
      label: {
        show: true,
        position: "top",
        color: "#4E5969",
        fontSize: FONT_SMALL,
        formatter: (params) => (params.dataIndex === 0 || params.dataIndex === categories.length - 1 ? compactCount(params.value) : "")
      }`;
}

function buildDonutOption(spec) {
  const series = spec.series[0];
  const values = getSeriesValues(series);
  const labelPolicy = getLabelPolicy(spec, 'outer_name_percent');
  const palette = ['#1677FF', '#0FC6C2', '#00B96B', '#FF7D00', '#F53F3F', '#6236FF'];
  const data = spec.categories.map((name, index) => ({
    name,
    value: values[index],
    color: palette[index % palette.length]
  }));

  return `
const data = ${JSON.stringify(data, null, 2)}.map((item) => ({
  ...item,
  itemStyle: {
    borderColor: "#FFFFFF",
    borderWidth: 3,
    color: new echarts.graphic.LinearGradient(0, 0, 1, 1, [
      { offset: 0, color: item.color },
      { offset: 1, color: mixWhite(item.color) }
    ])
  }
}));

var option = {
  backgroundColor: "#FFFFFF",
  animation: false,
  tooltip: {
    trigger: "item",
    backgroundColor: "#1D2129",
    borderWidth: 0,
    padding: 12,
    textStyle: { color: "#FFFFFF", fontSize: FONT_MAIN },
    formatter: (params) => \`\${params.name}<br/>占比：\${params.value.toFixed(2)}%\`
  },
  legend: {
    bottom: 8,
    icon: "circle",
    itemWidth: LEGEND_ICON,
    itemHeight: LEGEND_ICON,
    textStyle: { color: "#4E5969", fontSize: FONT_MAIN }
  },
  series: [
    {
      type: "pie",
      radius: ["40%", "68%"],
      center: ["50%", "45%"],
      avoidLabelOverlap: true,
      minAngle: 4,
      label: ${labelPolicy === 'tooltip_only' ? '{ show: false }' : `{
        show: true,
        formatter: "{b}\\n{d}%",
        alignTo: "edge",
        edgeDistance: "12%",
        color: "#4E5969",
        fontSize: FONT_MAIN,
        lineHeight: Math.round(FONT_MAIN * 1.35)
      }`},
      labelLine: ${labelPolicy === 'tooltip_only' ? '{ show: false }' : `{
        show: true,
        length: 16,
        length2: 12,
        smooth: 0.2
      }`},
      itemStyle: {
        borderColor: "#FFFFFF",
        borderWidth: 3
      },
      data
    }
  ]
};`;
}

function buildVerticalBarOption(spec) {
  const series = spec.series[0];
  const values = getSeriesValues(series);
  const unit = (series.unit || '').toLowerCase();
  const labelPolicy = getLabelPolicy(spec, 'top_value');
  const yAxisFormatter = unit === 'percent' ? 'percentLabel' : 'compactCount';
  const tooltipLabel = unit === 'percent' ? '占比' : (series.name || '数值');
  const tooltipValue = unit === 'percent'
    ? '${percentLabel(items[0].value)}'
    : '${compactCount(items[0].value)}';

  return `
const categories = ${JSON.stringify(spec.categories, null, 2)};
const values = ${JSON.stringify(values, null, 2)};

var option = {
  backgroundColor: "#FFFFFF",
  animation: false,
  tooltip: {
    trigger: "axis",
    axisPointer: { type: "shadow" },
    backgroundColor: "#1D2129",
    borderWidth: 0,
    padding: 12,
    textStyle: { color: "#FFFFFF", fontSize: FONT_MAIN },
    formatter: (items) => \`\${items[0].name}<br/>\${items[0].marker}${tooltipLabel}：${tooltipValue}\`
  },
  grid: {
    top: 56,
    left: 86,
    right: 36,
    bottom: 120
  },
  xAxis: {
    type: "category",
    data: categories,
    axisTick: { show: false },
    axisLine: { show: false },
    axisLabel: {
      color: "#86909C",
      fontSize: FONT_MAIN,
      rotate: categories.length > 6 ? 24 : 0,
      margin: 16
    }
  },
  yAxis: {
    type: "value",
    splitLine: { lineStyle: { color: "#E5E6EB", type: "dashed" } },
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: {
      color: "#86909C",
      fontSize: FONT_MAIN,
      formatter: ${yAxisFormatter}
    }
  },
  series: [
    {
      name: ${JSON.stringify(series.name || '数值')},
      type: "bar",
      data: values,
      barWidth: 34,
${buildTopBarLabelSnippet(labelPolicy, unit === 'percent')},
      itemStyle: {
        borderRadius: [8, 8, 0, 0],
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: "#1677FF" },
          { offset: 1, color: mixWhite("#1677FF") }
        ])
      }
    }
  ]
};`;
}

function buildRankedBarOption(spec) {
  const series = spec.series[0];
  const values = getSeriesValues(series);
  const labelPolicy = getLabelPolicy(spec, 'right_value');

  return `
const categories = ${JSON.stringify(spec.categories, null, 2)};
const values = ${JSON.stringify(values, null, 2)};

var option = {
  backgroundColor: "#FFFFFF",
  animation: false,
  tooltip: {
    trigger: "axis",
    axisPointer: { type: "shadow" },
    backgroundColor: "#1D2129",
    borderWidth: 0,
    padding: 12,
    textStyle: { color: "#FFFFFF", fontSize: FONT_MAIN },
    formatter: (items) => \`\${items[0].name}<br/>\${items[0].marker}${series.name || '数值'}：\${compactCount(items[0].value)}\`
  },
  grid: {
    top: 36,
    left: 180,
    right: 60,
    bottom: 28
  },
  xAxis: {
    type: "value",
    splitLine: { lineStyle: { color: "#E5E6EB", type: "dashed" } },
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: "#86909C", fontSize: FONT_MAIN, formatter: compactCount }
  },
  yAxis: {
    type: "category",
    inverse: true,
    data: categories,
    axisTick: { show: false },
    axisLine: { show: false },
    axisLabel: {
      color: "#4E5969",
      fontSize: FONT_MAIN
    }
  },
  series: [
    {
      name: ${JSON.stringify(series.name || '数值')},
      type: "bar",
      data: values,
      barWidth: 18,
${buildRightBarLabelSnippet(labelPolicy)},
      itemStyle: {
        borderRadius: [0, 9, 9, 0],
        color: new echarts.graphic.LinearGradient(1, 0, 0, 0, [
          { offset: 0, color: mixWhite("#1677FF") },
          { offset: 1, color: "#1677FF" }
        ])
      }
    }
  ]
};`;
}

function buildStackedPercentOption(spec) {
  const sampleSizes = Array.isArray(spec.sample_sizes) ? spec.sample_sizes : Array.isArray(spec.samples) ? spec.samples : null;
  const labelPolicy = getLabelPolicy(spec, 'inside_percent_threshold');

  return `
const categories = ${JSON.stringify(spec.categories, null, 2)};
const sampleSizes = ${JSON.stringify(sampleSizes, null, 2)};
const seriesData = ${JSON.stringify(
    spec.series.map((item) => ({
      name: item.name,
      unit: item.unit,
      values: getSeriesValues(item)
    })),
    null,
    2
  )};
const palette = { "正面": "#00B96B", "中性": "#0FC6C2", "负面": "#F53F3F" };

var option = {
  backgroundColor: "#FFFFFF",
  animation: false,
  color: ["#00B96B", "#0FC6C2", "#F53F3F"],
  tooltip: {
    trigger: "axis",
    axisPointer: { type: "shadow" },
    backgroundColor: "#1D2129",
    borderWidth: 0,
    padding: 12,
    textStyle: { color: "#FFFFFF", fontSize: FONT_MAIN },
    formatter: (items) => {
      const idx = items[0].dataIndex;
      const lines = items.map((item) => \`\${item.marker}\${item.seriesName}：\${item.value.toFixed(2)}%\`);
      if (sampleSizes && sampleSizes[idx] !== undefined) {
        lines.push(\`有效样本：\${compactCount(sampleSizes[idx])}\`);
      }
      return \`\${categories[idx]}<br/>\${lines.join("<br/>")}\`;
    }
  },
  legend: {
    bottom: 8,
    icon: "circle",
    itemWidth: LEGEND_ICON,
    itemHeight: LEGEND_ICON,
    textStyle: { color: "#4E5969", fontSize: FONT_MAIN }
  },
  grid: {
    top: 70,
    left: 90,
    right: 36,
    bottom: 130
  },
  xAxis: {
    type: "category",
    data: categories,
    axisTick: { show: false },
    axisLine: { show: false },
    axisLabel: {
      color: "#86909C",
      fontSize: FONT_MAIN,
      rotate: 24,
      margin: 18
    }
  },
  yAxis: {
    type: "value",
    min: 0,
    max: 100,
    splitLine: { lineStyle: { color: "#E5E6EB", type: "dashed" } },
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: {
      color: "#86909C",
      fontSize: FONT_MAIN,
      formatter: "{value}%"
    }
  },
  series: seriesData.map((item) => ({
    ...item,
    type: "bar",
    data: item.values,
    stack: "total",
    barWidth: 52,
    itemStyle: { color: palette[item.name] || "#1677FF", borderRadius: 0 },
${buildStackedLabelSnippet(labelPolicy)}
  }))
};`;
}

function buildGroupedBarOption(spec) {
  const labelPolicy = getLabelPolicy(spec, 'top_value');
  const labelFormatter = "isPercent ? percentLabel(value) : compactCount(value)";
  return `
const categories = ${JSON.stringify(spec.categories, null, 2)};
const seriesData = ${JSON.stringify(
    spec.series.map((item) => ({
      name: item.name,
      unit: item.unit,
      values: getSeriesValues(item)
    })),
    null,
    2
  )};
const palette = ["#1677FF", "#0FC6C2", "#00B96B", "#FF7D00", "#722ED1"];
const isPercent = seriesData.every((item) => (item.unit || "").toLowerCase() === "percent");

var option = {
  backgroundColor: "#FFFFFF",
  animation: false,
  tooltip: {
    trigger: "axis",
    axisPointer: { type: "shadow" },
    backgroundColor: "#1D2129",
    borderWidth: 0,
    padding: 12,
    textStyle: { color: "#FFFFFF", fontSize: FONT_MAIN }
  },
  legend: {
    bottom: 8,
    icon: "circle",
    itemWidth: LEGEND_ICON,
    itemHeight: LEGEND_ICON,
    textStyle: { color: "#4E5969", fontSize: FONT_MAIN }
  },
  grid: {
    top: 60,
    left: 90,
    right: 36,
    bottom: 120
  },
  xAxis: {
    type: "category",
    data: categories,
    axisTick: { show: false },
    axisLine: { show: false },
    axisLabel: {
      color: "#86909C",
      fontSize: FONT_MAIN,
      rotate: categories.length > 5 ? 18 : 0,
      margin: 16
    }
  },
  yAxis: {
    type: "value",
    splitLine: { lineStyle: { color: "#E5E6EB", type: "dashed" } },
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: "#86909C", fontSize: FONT_MAIN, formatter: isPercent ? percentLabel : compactCount }
  },
  series: seriesData.map((item, index) => ({
    ...item,
    type: "bar",
    data: item.values,
    barMaxWidth: 30,
${buildTopBarLabelSnippetWithFormatter(labelPolicy, labelFormatter)},
    itemStyle: {
      borderRadius: [8, 8, 0, 0],
      color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
        { offset: 0, color: palette[index % palette.length] },
        { offset: 1, color: mixWhite(palette[index % palette.length]) }
      ])
    }
  }))
};`;
}

function buildSingleLineOption(spec) {
  const series = spec.series[0];
  const values = getSeriesValues(series);
  const labelPolicy = getLabelPolicy(spec, 'key_points_only');
  const sorted = [...values].map((value, index) => ({ value, index })).sort((a, b) => b.value - a.value);
  const keyIndexes = [];
  if (sorted[0]) keyIndexes.push(sorted[0].index);
  for (const item of sorted.slice(1)) {
    if (!keyIndexes.includes(item.index) && Math.abs(item.index - keyIndexes[0]) > 2) {
      keyIndexes.push(item.index);
      break;
    }
  }
  keyIndexes.push(values.length - 1);

  return `
const categories = ${JSON.stringify(spec.categories, null, 2)};
const values = ${JSON.stringify(values, null, 2)};
const KEY_INDEXES = new Set(${JSON.stringify(keyIndexes)});

var option = {
  backgroundColor: "#FFFFFF",
  animation: false,
  tooltip: {
    trigger: "axis",
    axisPointer: { type: "line" },
    backgroundColor: "#1D2129",
    borderWidth: 0,
    padding: 12,
    textStyle: { color: "#FFFFFF", fontSize: FONT_MAIN },
    formatter: (items) => \`\${items[0].name}<br/>\${items[0].marker}${series.name || '数值'}：\${compactCount(items[0].value)}\`
  },
  grid: {
    top: 56,
    left: 82,
    right: 36,
    bottom: 80
  },
  xAxis: {
    type: "category",
    data: categories,
    axisTick: { show: false },
    axisLine: { show: false },
    axisLabel: {
      color: "#86909C",
      fontSize: FONT_MAIN,
      interval: Math.max(0, Math.floor(categories.length / 10)),
      margin: 16,
      formatter: (value) => value.length >= 5 && value.includes("-") ? value.slice(5) : value
    }
  },
  yAxis: {
    type: "value",
    splitLine: { lineStyle: { color: "#E5E6EB", type: "dashed" } },
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: "#86909C", fontSize: FONT_MAIN, formatter: compactCount }
  },
  series: [
    {
      name: ${JSON.stringify(series.name || '数值')},
      type: "line",
      smooth: true,
      symbol: "circle",
      symbolSize: 8,
      lineStyle: { width: 3, color: "#1677FF" },
      itemStyle: { color: "#1677FF" },
${buildLineLabelSnippet(labelPolicy)},
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: "rgba(22,119,255,0.30)" },
          { offset: 1, color: "rgba(22,119,255,0.04)" }
        ])
      },
      data: values
    }
  ]
};`;
}

function buildDualAxisTrendOption(spec) {
  const barSeries = spec.series[0];
  const lineSeries = spec.series[1];
  const labelPolicy = getLabelPolicy(spec, 'key_points_only');
  const barValues = getSeriesValues(barSeries);
  const lineValues = getSeriesValues(lineSeries);
  const sorted = [...lineValues].map((value, index) => ({ value, index })).sort((a, b) => b.value - a.value);
  const keyIndexes = [];
  if (sorted[0]) keyIndexes.push(sorted[0].index);
  for (const item of sorted.slice(1)) {
    if (!keyIndexes.includes(item.index) && Math.abs(item.index - keyIndexes[0]) > 2) {
      keyIndexes.push(item.index);
      break;
    }
  }
  keyIndexes.push(lineValues.length - 1);

  return `
const categories = ${JSON.stringify(spec.categories, null, 2)};
const barValues = ${JSON.stringify(barValues, null, 2)};
const lineValues = ${JSON.stringify(lineValues, null, 2)};
const KEY_INDEXES = new Set(${JSON.stringify(keyIndexes)});

var option = {
  backgroundColor: "#FFFFFF",
  animation: false,
  legend: {
    bottom: 8,
    icon: "circle",
    itemWidth: LEGEND_ICON,
    itemHeight: LEGEND_ICON,
    textStyle: { color: "#4E5969", fontSize: FONT_MAIN }
  },
  tooltip: {
    trigger: "axis",
    axisPointer: { type: "cross" },
    backgroundColor: "#1D2129",
    borderWidth: 0,
    padding: 12,
    textStyle: { color: "#FFFFFF", fontSize: FONT_MAIN }
  },
  grid: {
    top: 70,
    left: 82,
    right: 100,
    bottom: 108
  },
  xAxis: {
    type: "category",
    data: categories,
    axisTick: { show: false },
    axisLine: { show: false },
    axisLabel: {
      color: "#86909C",
      fontSize: FONT_MAIN,
      interval: Math.max(0, Math.floor(categories.length / 10)),
      margin: 16,
      formatter: (value) => value.length >= 5 && value.includes("-") ? value.slice(5) : value
    }
  },
  yAxis: [
    {
      type: "value",
      splitLine: { lineStyle: { color: "#E5E6EB", type: "dashed" } },
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: "#86909C", fontSize: FONT_MAIN, formatter: compactCount }
    },
    {
      type: "value",
      splitLine: { show: false },
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: "#86909C", fontSize: FONT_MAIN, formatter: compactCount }
    }
  ],
  series: [
    {
      name: ${JSON.stringify(barSeries.name || '柱状值')},
      type: "bar",
      data: barValues,
      barWidth: 18,
      itemStyle: {
        borderRadius: [9, 9, 0, 0],
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: "#1677FF" },
          { offset: 1, color: mixWhite("#1677FF") }
        ])
      }
    },
    {
      name: ${JSON.stringify(lineSeries.name || '折线值')},
      type: "line",
      yAxisIndex: 1,
      smooth: true,
      symbol: "circle",
      symbolSize: 8,
      lineStyle: { width: 4, color: "#0FC6C2" },
      itemStyle: { color: "#0FC6C2" },
${buildLineLabelSnippet(labelPolicy)},
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: "rgba(15,198,194,0.30)" },
          { offset: 1, color: "rgba(15,198,194,0.04)" }
        ])
      },
      data: lineValues
    }
  ]
};`;
}

function buildBarLineComboOption(spec) {
  const barSeries = spec.series[0];
  const lineSeries = spec.series[1];
  const labelPolicy = getLabelPolicy(spec, 'line_key_points_only');
  const barValues = getSeriesValues(barSeries);
  const lineValues = getSeriesValues(lineSeries);

  return `
const categories = ${JSON.stringify(spec.categories, null, 2)};
const barValues = ${JSON.stringify(barValues, null, 2)};
const lineValues = ${JSON.stringify(lineValues, null, 2)};

var option = {
  backgroundColor: "#FFFFFF",
  animation: false,
  legend: {
    bottom: 8,
    icon: "circle",
    itemWidth: LEGEND_ICON,
    itemHeight: LEGEND_ICON,
    textStyle: { color: "#4E5969", fontSize: FONT_MAIN }
  },
  tooltip: {
    trigger: "axis",
    axisPointer: { type: "shadow" },
    backgroundColor: "#1D2129",
    borderWidth: 0,
    padding: 12,
    textStyle: { color: "#FFFFFF", fontSize: FONT_MAIN }
  },
  grid: {
    top: 60,
    left: 90,
    right: 100,
    bottom: 120
  },
  xAxis: {
    type: "category",
    data: categories,
    axisTick: { show: false },
    axisLine: { show: false },
    axisLabel: {
      color: "#86909C",
      fontSize: FONT_MAIN,
      rotate: categories.length > 4 ? 18 : 0,
      margin: 16
    }
  },
  yAxis: [
    {
      type: "value",
      splitLine: { lineStyle: { color: "#E5E6EB", type: "dashed" } },
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: "#86909C", fontSize: FONT_MAIN, formatter: compactCount }
    },
    {
      type: "value",
      splitLine: { show: false },
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: "#86909C", fontSize: FONT_MAIN, formatter: compactCount }
    }
  ],
  series: [
    {
      name: ${JSON.stringify(barSeries.name || '柱状值')},
      type: "bar",
      data: barValues,
      barWidth: 28,
      itemStyle: {
        borderRadius: [8, 8, 0, 0],
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: "#F53F3F" },
          { offset: 1, color: mixWhite("#F53F3F") }
        ])
      }
    },
    {
      name: ${JSON.stringify(lineSeries.name || '折线值')},
      type: "line",
      yAxisIndex: 1,
      smooth: true,
      symbol: "circle",
      symbolSize: 8,
      lineStyle: { width: 4, color: "#0FC6C2" },
      itemStyle: { color: "#0FC6C2" },
${buildComboLineLabelSnippet(labelPolicy)},
      data: lineValues
    }
  ]
};`;
}

function buildJsFromSpec(spec, width, height) {
  if (!spec || typeof spec !== 'object') {
    fail('spec must be a JSON object');
  }

  const chartType = String(spec.chart_type || '').trim().toLowerCase();
  if (!Array.isArray(spec.categories)) {
    fail('spec.categories must be an array');
  }
  if (!Array.isArray(spec.series)) {
    fail('spec.series must be an array');
  }

  let optionCode;
  switch (chartType) {
    case 'donut':
    case 'pie':
      optionCode = buildDonutOption(spec);
      break;
    case 'bar':
    case 'vertical_bar':
      optionCode = buildVerticalBarOption(spec);
      break;
    case 'rank_bar':
    case 'ranked_bar':
      optionCode = buildRankedBarOption(spec);
      break;
    case 'stacked_percent_bar':
    case 'stacked_bar':
      optionCode = buildStackedPercentOption(spec);
      break;
    case 'grouped_bar':
      optionCode = buildGroupedBarOption(spec);
      break;
    case 'dual_axis_trend':
      if (spec.series.length < 2) {
        fail(`${chartType} requires at least 2 series`);
      }
      optionCode = buildDualAxisTrendOption(spec);
      break;
    case 'line':
      if (spec.series.length === 1) {
        optionCode = buildSingleLineOption(spec);
      } else {
        optionCode = buildDualAxisTrendOption(spec);
      }
      break;
    case 'bar_line_combo':
      if (spec.series.length < 2) {
        fail('bar_line_combo requires at least 2 series');
      }
      optionCode = buildBarLineComboOption(spec);
      break;
    default:
      fail(`unsupported chart_type: ${spec.chart_type}`);
  }

  return `${prelude(width, height)}\n${optionCode}\n`;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.jsOut && !args.output) {
    fail('please provide --js-out <path> and/or --output <path>');
  }

  const spec = loadSpec(args);
  const jsCode = buildJsFromSpec(spec, args.width, args.height);

  if (args.jsOut) {
    fs.writeFileSync(path.resolve(args.jsOut), jsCode);
    console.log(`JS WRITTEN: ${path.resolve(args.jsOut)}`);
  }

  if (args.output) {
    let jsPath = args.jsOut ? path.resolve(args.jsOut) : null;
    let tempJsPath = null;

    if (!jsPath) {
      tempJsPath = path.join(process.cwd(), `.tmp-${spec.chart_id || 'chart'}-${Date.now()}.js`);
      fs.writeFileSync(tempJsPath, jsCode);
      jsPath = tempJsPath;
    }

    execFileSync(
      'node',
      [
        RENDER_SCRIPT,
        '--js',
        jsPath,
        '--output',
        path.resolve(args.output),
        '--title',
        spec.title || spec.chart_id || 'Chart',
        '--template',
        path.resolve(args.template),
        '--width',
        String(args.width),
        '--height',
        String(args.height)
      ],
      { stdio: 'inherit' }
    );

    console.log(`PNG WRITTEN: ${path.resolve(args.output)}`);

    if (tempJsPath && fs.existsSync(tempJsPath)) {
      fs.unlinkSync(tempJsPath);
    }
  }
}

main();
