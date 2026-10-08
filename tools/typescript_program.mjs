// Shared TypeScript inputs/options for Provider execution and cache identity.
import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';

export const suffixes = new Set(['.ts', '.tsx', '.js', '.jsx', '.mts', '.cts', '.mjs', '.cjs']);
// Parser choice is independent of the language of the input file.
export function sourceLanguage(fileName) {
  const suffix = path.extname(fileName).toLowerCase();
  if (['.ts', '.tsx', '.mts', '.cts'].includes(suffix)) return 'typescript';
  if (['.js', '.jsx', '.mjs', '.cjs'].includes(suffix)) return 'javascript';
  return 'unknown';
}
export function createAnalysisProgram(root) {
  const files = [];
  function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const absolute = path.join(dir, entry.name);
      if (entry.isDirectory() && entry.name !== '.git') walk(absolute);
      else if (entry.isFile() && suffixes.has(path.extname(absolute).toLowerCase())) files.push(absolute);
    }
  }
  walk(root); files.sort();
  const options = { allowJs: true, checkJs: false, noEmit: true, target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.NodeNext, moduleResolution: ts.ModuleResolutionKind.NodeNext, skipLibCheck: true };
  return { files, program: ts.createProgram(files, options) };
}
