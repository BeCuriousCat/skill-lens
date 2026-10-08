#!/usr/bin/env node
// Recompute dependency resolution before looking up a TypeScript cache entry.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createAnalysisProgram } from './typescript_program.mjs';

const root = path.resolve(process.argv[2]);
const { program } = createAnalysisProgram(root);
const inputs = new Set(program.getSourceFiles().map(source => path.resolve(source.fileName)));
const compiler = fileURLToPath(import.meta.resolve('typescript'));
inputs.add(compiler);
// NodeNext classification and module exports can depend on package metadata.
for (const input of [...inputs, path.join(root, '_source_root')]) {
  let directory = path.dirname(input);
  while (true) {
    const metadata = path.join(directory, 'package.json');
    if (fs.existsSync(metadata) && fs.statSync(metadata).isFile()) inputs.add(metadata);
    const parent = path.dirname(directory);
    if (parent === directory) break;
    directory = parent;
  }
}
console.log(JSON.stringify([...inputs].sort()));
