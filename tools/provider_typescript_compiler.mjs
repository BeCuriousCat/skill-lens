#!/usr/bin/env node
// TypeScript compiler API candidate. It reports local symbols and resolved calls;
// unresolved external modules remain diagnostics instead of fabricated edges.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import ts from 'typescript';
import { createAnalysisProgram, sourceLanguage } from './typescript_program.mjs';
import { isSourceStatement, nearestSourceStatement, sourceStatementFact } from './typescript_source_context.mjs';

const PROVIDER = { id: 'typescript-compiler', version: '0.3.0+ts5.6.2' };
const args = Object.fromEntries(process.argv.slice(2).map((v, i, a) => v.startsWith('--') ? [v.slice(2), a[i + 1]] : []).filter(Boolean));
const root = path.resolve(args['source-dir']);
const revision = args.revision;
const caseId = args['case-id'];
const out = path.resolve(args.out);
const stable = (prefix, value) => `${prefix}:${crypto.createHash('sha1').update(value).digest('hex').slice(0, 16)}`;
const { files, program } = createAnalysisProgram(root);
const rel = p => path.relative(root, p).split(path.sep).join('/');
const evidence = [], nodes = [], edges = [], diagnostics = [];
const nodeBySymbol = new Map();
const pendingResolutions = [];
const sourceRefs = new Map();
const addEvidence = (file, start, end, quote) => { const lines = `${start.line}-${Math.max(start.line, end.line)}`; const id = stable('evidence', `${revision}:${file}:${lines}:${quote}`); const item = { id, sourceType: 'code', source: { revision, path: file, lines }, quote: quote.slice(0, 500), confidence: 'high' }; if (!sourceRefs.has(id)) { evidence.push(item); sourceRefs.set(id, item); } return id; };
const sourceSpan = node => {
  const source = node.getSourceFile();
  const start = source.getLineAndCharacterOfPosition(node.getStart());
  const end = source.getLineAndCharacterOfPosition(node.end);
  return { line: start.line + 1, col: start.character, endLine: end.line + 1, endCol: end.character };
};
const addNode = (id, type, label, attrs, ev) => { nodes.push({ id, type, label, attributes: attrs, evidence: [ev] }); };
const artifactEv = addEvidence('.', { line: 1 }, { line: 1 }, `TypeScript compiler found ${files.length} supported source files`);
const artifactId = stable('node', `artifact:${caseId}:${revision}`);
addNode(artifactId, 'Artifact', caseId, {
  sourceFileCount: files.length,
  typescriptFileCount: files.filter(file => sourceLanguage(file) === 'typescript').length,
  javascriptFileCount: files.filter(file => sourceLanguage(file) === 'javascript').length,
}, artifactEv);
const checker = program.getTypeChecker();
const componentByFile = new Map();
for (const source of program.getSourceFiles().filter(s => !s.isDeclarationFile && s.fileName.startsWith(root))) {
  const file = rel(source.fileName); const text = source.getFullText(); const ev = addEvidence(file, { line: 1 }, { line: source.getLineAndLineMap ? source.getLineAndLineMap().length : text.split('\n').length }, `source file ${file} parsed by TypeScript compiler`); const component = stable('node', `component:${caseId}:${file}:${revision}`); componentByFile.set(source.fileName, component); addNode(component, 'Component', file, { suffix: path.extname(file), language: sourceLanguage(file), file: true }, ev); edges.push({ id: stable('edge', `${artifactId}:contains:${component}`), from: artifactId, to: component, type: 'contains' });
  const statementIds = new Map();
  function visit(node, owner = component) {
    const span = sourceSpan(node); const textNode = node.getText(source).replace(/\s+/g, ' ').slice(0, 180);
    if (isSourceStatement(node)) {
      const fact = sourceStatementFact(node, source);
      const ref = addEvidence(file, { line: fact.startLine }, { line: fact.endLine }, fact.quote);
      const id = stable('node', `statement:${caseId}:${file}:${span.line}:${span.col}:${node.end}:${revision}`);
      statementIds.set(node, { id, ref });
      addNode(id, 'Scenario', `source statement in ${file}:${span.line}`, fact.attributes, ref);
      edges.push({ id: stable('edge', `${owner}:contains:${id}`), from: owner, to: id, type: 'contains', evidence: [ref] });
    }
    let symbolNode;
    if (ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node) || ts.isClassDeclaration(node) || ts.isInterfaceDeclaration(node) || ts.isEnumDeclaration(node) || ts.isTypeAliasDeclaration(node) || (ts.isVariableDeclaration(node) && node.initializer && (ts.isArrowFunction(node.initializer) || ts.isFunctionExpression(node.initializer)))) {
      const nameNode = node.name; const name = nameNode?.getText(source) || '<anonymous>'; const evDef = addEvidence(file, { line: span.line }, { line: span.endLine }, `definition ${name}: ${textNode}`); symbolNode = stable('node', `symbol:${caseId}:${file}:${name}:${span.line}:${revision}`); addNode(symbolNode, 'Capability', `${file}:${name}`, { kind: 'symbol', name, syntaxType: node.kind, span: `${span.line}:${span.col}-${span.endLine}` }, evDef); edges.push({ id: stable('edge', `${component}:declares:${symbolNode}`), from: component, to: symbolNode, type: 'declares' }); const symbol = nameNode && checker.getSymbolAtLocation(nameNode); if (symbol) nodeBySymbol.set(symbol, symbolNode);
      if (owner !== component) edges.push({ id: stable('edge', `${owner}:contains:${symbolNode}`), from: owner, to: symbolNode, type: 'contains', evidence: [evDef] });
    }
    const anonymousCallback = (ts.isArrowFunction(node) || ts.isFunctionExpression(node)) &&
      !(ts.isVariableDeclaration(node.parent) && node.parent.initializer === node);
    let callbackNode;
    if (anonymousCallback) {
      const evCallback = addEvidence(file, { line: span.line }, { line: span.endLine }, `callback: ${textNode}`);
      callbackNode = stable('node', `callback:${caseId}:${file}:${span.line}:${span.col}:${node.end}:${revision}`);
      addNode(callbackNode, 'Component', `callback in ${file}`, { kind: 'callback', span: `${span.line}:${span.col}-${span.endLine}` }, evCallback);
      edges.push({ id: stable('edge', `${owner}:contains:${callbackNode}`), from: owner, to: callbackNode, type: 'contains', evidence: [evCallback] });
    }
    if (ts.isImportDeclaration(node)) { const evImport = addEvidence(file, { line: span.line }, { line: span.endLine }, `import ${textNode}`); const id = stable('node', `import:${caseId}:${file}:${span.line}:${revision}`); addNode(id, 'Component', `import in ${file}`, { kind: 'import', module: node.moduleSpecifier.getText(source) }, evImport); edges.push({ id: stable('edge', `${component}:imports:${id}`), from: component, to: id, type: 'imports' }); const clause = node.importClause; if (clause) { const bindings = []; if (clause.name) bindings.push(clause.name); if (clause.namedBindings && ts.isNamedImports(clause.namedBindings)) bindings.push(...clause.namedBindings.elements.map(e => e.name)); for (const binding of bindings) { const symbol = checker.getSymbolAtLocation(binding); if (symbol && symbol.flags & ts.SymbolFlags.Alias) pendingResolutions.push({ from: id, candidates: [checker.getAliasedSymbol(symbol)], evidence: [evImport] }); } } }
    if (ts.isCallExpression(node) || ts.isNewExpression(node)) { const evCall = addEvidence(file, { line: span.line }, { line: span.endLine }, `call ${textNode}`); const id = stable('node', `call:${caseId}:${file}:${span.line}:${span.col}:${node.end}:${textNode}:${revision}`); addNode(id, 'Scenario', `call in ${file}`, { kind: 'call', expression: textNode, span: `${span.line}:${span.col}-${span.endLine}:${span.endCol}` }, evCall); edges.push({ id: stable('edge', `${component}:invokes:${id}`), from: component, to: id, type: 'invokes' }); edges.push({ id: stable('edge', `${owner}:contains:${id}`), from: owner, to: id, type: 'contains', evidence: [evCall] }); const sig = checker.getResolvedSignature(node); const decl = sig?.getDeclaration(); const expressionSymbol = checker.getSymbolAtLocation(node.expression); const symbol = expressionSymbol && expressionSymbol.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(expressionSymbol) : expressionSymbol; const declarationSymbol = decl && (checker.getSymbolAtLocation(decl.name) || checker.getSymbolAtLocation(decl)); pendingResolutions.push({ from: id, candidates: [symbol, declarationSymbol], evidence: [evCall] }); }
    if (ts.isCallExpression(node) || ts.isNewExpression(node)) {
      const statement = nearestSourceStatement(node);
      const info = statementIds.get(statement);
      if (info) {
        const { id: from, ref } = info;
        const to = stable('node', `call:${caseId}:${file}:${span.line}:${span.col}:${node.end}:${textNode}:${revision}`);
        edges.push({ id: stable('edge', `${from}:contains:${to}`), from, to, type: 'contains', evidence: [ref] });
      }
    }
    const childOwner = callbackNode || symbolNode || owner;
    ts.forEachChild(node, child => visit(child, childOwner));
  }
  visit(source);
}
// Resolve only after every local declaration has been indexed. TypeScript's
// lexical symbol lookup is independent of source traversal order; the graph
// index must have the same property (including forward declarations/cycles).
for (const { from, candidates, evidence: refs } of pendingResolutions) {
  const target = candidates.map(symbol => symbol && nodeBySymbol.get(symbol)).find(Boolean);
  if (target) edges.push({ id: stable('edge', `${from}:resolves-symbol:${target}`), from, to: target, type: 'resolves-symbol', evidence: refs });
}
for (const diagnostic of ts.getPreEmitDiagnostics(program)) { if (diagnostic.file && diagnostic.file.fileName.startsWith(root)) diagnostics.push({ code: 'typescript-compiler-diagnostic', severity: 'info', message: ts.flattenDiagnosticMessageText(diagnostic.messageText, ' '), path: rel(diagnostic.file.fileName) }); }
diagnostics.push({ code: 'typescript-compiler-boundary', severity: 'info', message: 'TypeScript compiler facts include local symbol and resolved-call candidates; runtime dispatch, external package semantics, and framework behavior remain unresolved' });
diagnostics.push({ code: 'typescript-source-context-boundary', severity: 'info', message: 'Statement facts retain lexical if/else and try/catch/finally context within the nearest function. They do not establish reachability, execution order, state values, short-circuit/loop/switch conditions, or runtime success; source excerpts may be truncated.' });
const result = { schema: 'skill-lens.graph-patch.v0.1', provider: PROVIDER, revision, caseId, nodes, edges, evidence, diagnostics };
fs.mkdirSync(path.dirname(out), { recursive: true }); fs.writeFileSync(out, JSON.stringify(result, null, 2) + '\n');
console.log(`${caseId}: files=${files.length} nodes=${nodes.length} edges=${edges.length}`);
