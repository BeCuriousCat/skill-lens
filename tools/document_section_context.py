"""Bounded documentation context, without workflow or execution inference.

Only existing doc Evidence with valid Markdown heading paths is eligible.
Numbered sibling sections are reading context, never inferred execution edges.
"""

from collections import Counter
import math
import re


STEP = re.compile(r'^(?:step|stage|phase)\s+(\d+)\b', re.IGNORECASE)
MAX_GROUPS = 3
MAX_SIBLINGS = 8
MAX_SPANS_PER_SECTION = 24

# Question words are deliberately mapped to document vocabulary instead of
# using an unconstrained embedding/search layer. The aliases are retrieval
# hints only; they never become Evidence or semantic claims.
QUESTION_TERMS = {
    'generate': {'generate', 'generation', 'create', 'creating', 'edit', 'editing', 'modify', 'image'},
    'batch': {'batch', 'multiple', 'bulk', 'single', 'asset', 'assets'},
    'output': {'output', 'save', 'saved', 'path', 'workspace', 'artifact', 'file'},
    'audience': {'audience', 'reader', 'readers', 'role', 'level', 'grade', 'age', 'explain'},
    'research': {'research', 'interview', 'discover', 'question', 'questions', 'user'},
    'structure': {'structure', 'anatomy', 'disclosure', 'skill', 'section', 'reference'},
    'test': {'test', 'tests', 'eval', 'evaluation', 'grade', 'grading', 'benchmark', 'run', 'runs'},
}


def query_vocabulary(question, term_function):
    terms = set(morphology(term_function(question)))
    lowered = str(question).casefold()
    for marker, aliases in QUESTION_TERMS.items():
        if marker in lowered or any(alias in terms for alias in aliases):
            terms.update(aliases)
    return morphology(terms)


def morphology(words):
    """Conservative lexical alternatives used only by this experimental route."""
    result = set(words)
    for word in words:
        if not word.isascii():
            continue
        if len(word) > 5 and word.endswith('ing'):
            stem = word[:-3]
            result.update((stem, stem + 'e'))
            if len(stem) > 2 and stem[-1] == stem[-2]:
                result.add(stem[:-1])
        elif len(word) > 4 and word.endswith('ed'):
            result.update((word[:-2], word[:-1]))
        elif len(word) > 4 and word.endswith('ies'):
            result.add(word[:-3] + 'y')
        elif len(word) > 3 and word.endswith('s') and not word.endswith('ss'):
            result.add(word[:-1])
    return result


def section_requests(graph, primary, question, term_function, max_groups=None, preferred_terms=None):
    """Return source pairs ranked by headings plus numbered sibling context.

    Scope includes repository, revision, file and the full heading path. A
    group is an ordinary leaf section, or numbered children of one exact
    parent heading. Ranking never reads evaluator fields or case identities.
    """
    refs = {ref['id']: ref for ref in graph.get('evidence', [])}
    sections = {}
    for node in graph.get('nodes', []):
        attrs = node.get('attributes', {})
        heading = attrs.get('headingPath')
        if (attrs.get('kind') != 'markdown-span' or not isinstance(heading, list)
                or not heading or not all(isinstance(value, str) and value for value in heading)):
            continue
        for ref_id in node.get('evidence', []):
            ref = refs.get(ref_id, {})
            source = ref.get('source', {})
            location = re.fullmatch(r'(\d+)(?:-(\d+))?', str(source.get('lines', '')))
            if (ref.get('sourceType') != 'doc' or not location or not source.get('path')
                    or not source.get('revision')):
                continue
            first, last = int(location[1]), int(location[2] or location[1])
            if first < 1 or last < first:
                continue
            key = source.get('repository'), source['revision'], source['path'], tuple(heading)
            sections.setdefault(key, []).append({
                'pair': (node['id'], ref_id), 'first': first, 'last': last,
                'quote': ref.get('quote', ''), 'block': attrs.get('blockType'),
            })
    selected_pairs = {(item['nodeId'], item['evidenceId']) for item in primary['selected']}
    query = query_vocabulary(question, term_function)
    preferred_terms = set(preferred_terms or ())
    frequency = Counter()
    vocabulary = {}
    for key, spans in sections.items():
        title = morphology(term_function(key[-1][-1]))
        body = morphology(term_function(' '.join(span['quote'] for span in spans)))
        vocabulary[key] = title, body
        frequency.update(title | body)
    groups = {}
    for key in sections:
        step = STEP.match(key[-1][-1])
        group = (*key[:3], key[-1][:-1], 'numbered') if step else (*key, 'section')
        groups.setdefault(group, []).append(key)
    ranked = []
    gaps = []
    for group, keys in groups.items():
        numbered = group[-1] == 'numbered'
        numbers = [int(STEP.match(key[-1][-1])[1]) for key in keys] if numbered else []
        if numbered and len(numbers) != len(set(numbers)):
            gaps.append({'kind': 'markdown-section', 'sourcePath': group[2],
                         'reason': 'duplicate numbered sibling labels; section group excluded'})
            continue
        title_terms = set().union(*(vocabulary[key][0] for key in keys))
        body_terms = set().union(*(vocabulary[key][1] for key in keys))
        anchored = any(span['pair'] in selected_pairs for key in keys for span in sections[key])
        # Ordinary section context requires an existing primary anchor. A
        # numbered group can also be discovered through its literal headings.
        if not anchored and (not numbered or not query & title_terms):
            continue
        matched = query & (title_terms | body_terms)
        if not matched:
            continue
        score = sum(math.log1p((len(sections) + 1) / (frequency[word] + 1))
                    * (3 if word in title_terms else 1) for word in matched)
        ranked.append((anchored, score, repr(group), keys))
    ranked.sort(key=lambda entry: (-entry[0], -entry[1], entry[2]))
    group_limit = MAX_GROUPS if max_groups is None else max(1, int(max_groups))
    group_queues = []
    for anchored, group_score, _, keys in ranked[:group_limit]:
        def section_rank(key):
            title, body = vocabulary[key]
            score = sum(math.log1p((len(sections) + 1) / (frequency[word] + 1))
                        * (3 if word in title else 1) for word in query & (title | body))
            return -score, min(span['first'] for span in sections[key]), repr(key)
        ordered_keys = sorted(keys, key=section_rank)
        if len(ordered_keys) > MAX_SIBLINGS:
            gaps.append({'kind': 'markdown-section', 'sourcePath': ordered_keys[0][2],
                         'reason': 'numbered sibling count limit; remaining sections excluded'})
        queues = []
        for key in ordered_keys[:MAX_SIBLINGS]:
            spans = sorted(sections[key], key=lambda span: (span['first'], -span['last'], span['pair']))
            # A list container can carry many paragraphs. Preserve the
            # alternatives so an oversized container does not hide its body.
            spans = [span for span in spans if span['block'] != 'heading_open']
            if len(spans) > MAX_SPANS_PER_SECTION:
                gaps.append({'kind': 'markdown-section', 'sourcePath': key[2],
                             'reason': 'section span count limit; remaining spans excluded'})
            queues.append(spans[:MAX_SPANS_PER_SECTION])
        # Complete the most relevant section before spending capacity on
        # other siblings. Qualifiers late in a section must not be starved
        # by generic introductions from every other numbered step.
        group_requests = [span['pair'] for queue in queues for span in queue]
        if preferred_terms:
            def preferred_rank(pair):
                quote = refs.get(pair[1], {}).get('quote', '').casefold()
                return (-sum(term in quote for term in preferred_terms),
                        str(refs.get(pair[1], {}).get('source', {}).get('lines', '')))
            group_requests.sort(key=preferred_rank)
        group_queues.append((anchored, group_score, group_requests))
    for _, _, _, keys in ranked[group_limit:]:
        gaps.append({'kind': 'markdown-section', 'sourcePath': keys[0][2],
                     'reason': 'section group count limit; remaining context excluded'})
    # Preserve the historical round-robin request order for the generic
    # context contract. The explanation engine may opt into score-first order
    # through its own bounded ranking layer.
    # Return the first span of each selected section before later body spans.
    # This keeps a section's identity and its most relevant paragraph inside a
    # small downstream budget; callers can then expand the retained sections.
    requests = []
    queues = [queue for _, _, queue in group_queues]
    if preferred_terms:
        # A direct preferred hit is a representative even when another
        # heading has a higher aggregate score.
        preferred_pairs = []
        ordinary_pairs = []
        for queue in queues:
            for pair in queue:
                quote = refs.get(pair[1], {}).get('quote', '').casefold()
                (preferred_pairs if any(term in quote for term in preferred_terms) else ordinary_pairs).append(pair)
        queues = [preferred_pairs, ordinary_pairs]
        # Preserve direct workflow statements as atomic representatives. A
        # phrase such as "spawn two subagents" is more informative than a
        # neighboring generic grading paragraph even when both share a scope.
        direct = []
        seen = set()
        for pair in preferred_pairs:
            quote = refs.get(pair[1], {}).get('quote', '').casefold()
            if any(term in quote for term in preferred_terms) and pair not in seen:
                direct.append(pair)
                seen.add(pair)
        queues = [direct, [pair for pair in preferred_pairs if pair not in seen], ordinary_pairs]
    # Within a queue, retain the most query-bearing spans first. This is still
    # lexical retrieval, but prevents a generic lead paragraph from starving a
    # concrete example or output contract later in the same section.
    def span_score(pair):
        ref = refs.get(pair[1], {})
        words = morphology(term_function(ref.get('quote', '')))
        title_terms = set()
        for node in graph.get('nodes', []):
            if node.get('id') == pair[0]:
                title_terms = morphology(term_function(' '.join(node.get('attributes', {}).get('headingPath', []))))
                break
        matched = query & words
        heading_matches = query & title_terms
        weight = sum(math.log1p((len(sections) + 1) / (frequency[word] + 1)) for word in matched)
        weight += 2 * len(heading_matches)
        weight += 8 * len(preferred_terms & words)
        return -weight, -len(matched), str(ref.get('source', {}).get('lines', ''))
    queues = [sorted(queue, key=span_score) for queue in queues]
    for index in range(max((len(queue) for queue in queues), default=0)):
        for queue in queues:
            if index < len(queue):
                requests.append(queue[index])
    return list(dict.fromkeys(requests)), gaps
