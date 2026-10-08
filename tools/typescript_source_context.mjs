// Source-visible statement and lexical context only; no reachability analysis.
import ts from 'typescript';

const compact = (node, source) => node.getText(source).replace(/\s+/g, ' ').trim();
export function isSourceStatement(node) {
  return ts.isVariableStatement(node) || ts.isExpressionStatement(node) ||
    ts.isReturnStatement(node) || ts.isThrowStatement(node);
}

export function nearestSourceStatement(node) {
  for (let parent = node.parent; parent; parent = parent.parent) {
    // A callback body is a separate lexical execution scope. Its declaration
    // site is not a condition under which a later callback invocation runs.
    if (ts.isFunctionLike(parent)) return null;
    if (isSourceStatement(parent)) return parent;
  }
  return null;
}

export function sourceStatementFact(node, source) {
  const span = item => {
    const start = source.getLineAndCharacterOfPosition(item.getStart());
    const end = source.getLineAndCharacterOfPosition(item.end);
    return { startLine: start.line + 1, endLine: end.line + 1 };
  };
  const context = [];
  let child = node;
  for (let parent = node.parent; parent; child = parent, parent = parent.parent) {
    if (ts.isFunctionLike(parent)) break;
    if (ts.isIfStatement(parent)) {
      const condition = compact(parent.expression, source);
      context.push({ kind: 'if', branch: child === parent.thenStatement ? 'then' : child === parent.elseStatement ? 'else' : 'condition',
        condition: condition.slice(0, 240), truncated: condition.length > 240, ...span(parent.expression) });
    } else if (ts.isConditionalExpression(parent)) {
      const condition = compact(parent.condition, source);
      context.push({ kind: 'conditional-expression', branch: child === parent.whenTrue ? 'true' : child === parent.whenFalse ? 'false' : 'condition',
        condition: condition.slice(0, 240), truncated: condition.length > 240, ...span(parent.condition) });
    } else if (ts.isTryStatement(parent)) {
      const region = child === parent.tryBlock ? 'try' : child === parent.catchClause ? 'catch' : child === parent.finallyBlock ? 'finally' : 'unknown';
      const entry = { kind: 'exception-region', region, ...span(child) };
      if (region === 'catch' && parent.catchClause.variableDeclaration) entry.binding = compact(parent.catchClause.variableDeclaration.name, source);
      context.push(entry);
    }
  }
  context.reverse();
  const expression = compact(node, source);
  const location = span(node);
  const startLine = Math.min(location.startLine, ...context.map(item => item.startLine));
  const endLine = Math.max(location.endLine, ...context.map(item => item.endLine));
  const contextText = context.map(item => item.kind === 'exception-region'
    ? `${item.region} region (${item.startLine}-${item.endLine})`
    : `${item.kind} ${item.branch}: ${item.condition} (${item.startLine}-${item.endLine})`).join('; ');
  const quote = `${contextText ? `lexical context ${contextText}; ` : ''}source statement ${expression}`;
  return {
    attributes: { kind: 'source-statement', statementKind: ts.isVariableStatement(node) ? 'VariableStatement'
      : ts.isExpressionStatement(node) ? 'ExpressionStatement' : ts.isReturnStatement(node) ? 'ReturnStatement' : 'ThrowStatement',
      expression: expression.slice(0, 400), expressionTruncated: expression.length > 400,
      lexicalContext: context, staticOnly: true, statementLines: `${location.startLine}-${location.endLine}`,
      evidenceExcerptTruncated: quote.length > 500 },
    startLine, endLine, quote,
  };
}
