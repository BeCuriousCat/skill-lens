#!/usr/bin/env node
// Experimental source-span Provider. Markdown instructions remain source claims, not observed execution.

import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { join, relative, extname } from 'node:path';
import MarkdownIt from 'markdown-it';

const PROVIDER = { id: 'markdown-sections', version: '0.2.0' };
const parser = new MarkdownIt('commonmark').enable('table');

function stable(kind, value) {
  return `${kind}:${createHash('sha1').update(value).digest('hex').slice(0, 16)}`;
}

function filesUnder(root) {
  const files = [];
  function visit(dir) {
    for (const entry of readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      if (entry.name === '.git' || entry.name === 'node_modules' || entry.isSymbolicLink()) continue;
      const path = join(dir, entry.name);
      if (entry.isDirectory()) visit(path);
      else if (entry.isFile() && ['.md', '.markdown'].includes(extname(entry.name).toLowerCase())) files.push(path);
    }
  }
  visit(root);
  return files;
}

function evidence(revision, path, lines, quote) {
  return {
    id: stable('evidence', `${revision}:${path}:${lines}:${quote}`),
    sourceType: 'doc', source: { revision, path, lines }, quote, confidence: 'high',
  };
}

export function scan(root, revision, caseId) {
  const graph = { schema: 'skill-lens.graph-patch.v0.1', provider: PROVIDER, caseId, revision,
    nodes: [], edges: [], evidence: [], diagnostics: [] };
  const evidenceIds = new Set();
  const files = filesUnder(root);
  const artifactId = stable('node', `artifact:${caseId}:${revision}`);
  const treeEvidence = evidence(revision, '.', 'tree', `Markdown Provider found ${files.length} Markdown files`);
  graph.evidence.push(treeEvidence);
  graph.nodes.push({ id: artifactId, type: 'Artifact', label: caseId,
    attributes: { markdownFileCount: files.length }, evidence: [treeEvidence.id] });

  for (const file of files) {
    const path = relative(root, file).split('\\').join('/');
    const source = readFileSync(file, 'utf8');
    const lines = source.split(/\r?\n/);
    const fileId = stable('node', `component:${caseId}:${path}:${revision}`);
    const fileEvidence = evidence(revision, path, `1-${Math.max(1, lines.length)}`, `Markdown file ${path} exists`);
    graph.evidence.push(fileEvidence);
    graph.nodes.push({ id: fileId, type: 'Component', label: path,
      attributes: { suffix: extname(file).toLowerCase() }, evidence: [fileEvidence.id] });
    graph.edges.push({ id: stable('edge', `${artifactId}:contains:${fileId}`), from: artifactId, to: fileId, type: 'contains' });

    const headingStack = [];
    // YAML is metadata, not a setext Markdown heading. Mask it while preserving
    // line offsets, and keep its exact source as a separately typed span.
    let frontmatterEnd = -1;
    if (lines[0]?.trim() === '---') {
      frontmatterEnd = lines.findIndex((line, index) => index > 0 && ['---', '...'].includes(line.trim()));
    }
    const body = frontmatterEnd < 0 ? source : lines.map((line, index) => index <= frontmatterEnd ? '' : line).join('\n');
    const tokens = parser.parse(body, {});
    if (frontmatterEnd > 0) tokens.unshift({ type: 'frontmatter', map: [0, frontmatterEnd + 1] });
    for (let index = 0; index < tokens.length; index += 1) {
      const token = tokens[index];
      const kind = token.type;
      if (!['frontmatter', 'table_open', 'heading_open', 'paragraph_open', 'fence', 'code_block', 'bullet_list_open', 'ordered_list_open'].includes(kind) || !token.map) continue;
      const [start, end] = token.map;
      if (start >= end) continue;
      const quote = lines.slice(start, end).join('\n');
      if (!quote.trim()) continue;
      if (kind === 'heading_open') {
        const depth = Number(token.tag.slice(1));
        headingStack.length = depth - 1;
        headingStack[depth - 1] = tokens[index + 1]?.content || quote;
      }
      const location = `${start + 1}-${end}`;
      const ref = evidence(revision, path, location, quote);
      const nodeId = stable('node', `markdown-span:${caseId}:${path}:${kind}:${location}:${quote}:${revision}`);
      if (!evidenceIds.has(ref.id)) {
        graph.evidence.push(ref);
        evidenceIds.add(ref.id);
      }
      graph.nodes.push({ id: nodeId, type: 'Component', label: `${path}:${start + 1}`,
        attributes: { kind: 'markdown-span', blockType: kind, headingPath: headingStack.filter(Boolean) },
        evidence: [ref.id] });
      graph.edges.push({ id: stable('edge', `${fileId}:contains:${nodeId}`), from: fileId, to: nodeId,
        type: 'contains', evidence: [ref.id] });
    }
  }
  if (!files.length) graph.diagnostics.push({ code: 'no-markdown-files', severity: 'warning', message: 'no Markdown documents were found' });
  graph.diagnostics.push({ code: 'markdown-source-boundary', severity: 'info',
    message: 'Markdown spans are source text only; they do not prove host behavior or runtime execution' });
  return graph;
}

function main() {
  const args = {};
  for (let index = 2; index < process.argv.length; index += 2) args[process.argv[index]] = process.argv[index + 1];
  for (const name of ['--source-dir', '--revision', '--case-id', '--out']) {
    if (!args[name]) throw new Error(`missing ${name}`);
  }
  const graph = scan(args['--source-dir'], args['--revision'], args['--case-id']);
  writeFileSync(args['--out'], `${JSON.stringify(graph, null, 2)}\n`);
  process.stdout.write(`${graph.nodes.length} nodes, ${graph.evidence.length} evidence references\n`);
}

if (process.argv[1]?.endsWith('provider_markdown_sections.mjs')) main();
