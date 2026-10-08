# Skill Lens visual reports

Scope: the portable offline report, not the deferred M8 application shell.
Reader task: understand one integration, then verify a claim against its source.

The user requested the design aesthetics of `diagram-design`. This report
adopts its editorial hierarchy, deletion-first density, one focal color,
orthogonal connectors, fine rules, and overview/detail separation. The renderer
and interactions are implemented locally; consuming Skill Lens does not require
the external design skill. The project-specific palette below is an initial
implementation choice, not a user-confirmed brand identity.

## Tokens

| Role | Light | Dark |
| --- | --- | --- |
| paper | #f7f5ef | #191e1c |
| panel | #fffdf8 | #212824 |
| ink | #282f2c | #e9eee8 |
| muted | #59645f | #b3beb7 |
| rule | #d7dcd5 | #424d46 |
| accent | #24695d | #9acdbb |
| accent tint | #e6eee8 | #2a4036 |

The title uses a serif stack, body and node names a sans stack, identifiers a
mono stack. No network fonts are loaded: Instrument Serif/Geist are used only
when already installed, otherwise local Georgia/Songti/system fonts substitute.
This preserves offline portability rather than claiming exact font fidelity.
No shadows, decorative gradients, or invented scores.

## Composition and meaning

- A quiet header, source identity, and analysis boundary precede the report.
- Search and six existing Projection types organize the explanation list.
  Paginate at 12 items; all objects remain searchable, including unknowns.
- The reader opens one complete label and summary at a time.
- Citation tree: one explanation and at most four attached Evidence sources.
  Its arrows mean citations, never execution or calls. Extra sources are paged,
  and the full source text remains available below the figure.
- Source-relation slice: at most four independent original Graph edges per
  figure, eight node appearances. Repeated node appearances do not imply
  different identities. Full IDs, attributes, and edge Evidence remain available.
- Document-only containment/declaration is labeled document structure. A shared
  document parent is drawn once with its children. Span names come from headings
  or actual opening words; paths and line numbers are supporting provenance.
  A single quote or file-existence record is read directly, without a citation
  diagram. Mechanism reports begin with meaningful content units.
- With a mechanism interpretation, the reader starts in a meaning browser:
  mechanism steps, intended audiences, and individual expression rules. The
  source browser remains a separate mode with all original objects. A role's
  applicable rules form a bounded tree, one role and at most four named rules;
  the edge means a documented requirement for that reader, not Agent messages.
  Exact excerpts locate role rows even when several concepts cite one table.
- Missing edges stay missing. Display ordering is not execution ordering.
- An optional instruction-mechanism interpretation appears before the inventory.
  It separates cited requirements, analyst interpretations, illustrative outputs,
  and unknowns. Its dashed overview connectors mean explanatory ordering only;
  they are explicitly not Evidence Graph edges or observed execution. Exact
  prompts and the core explanation remain readable without JavaScript.
- Graph labels may be abbreviated for fit; full labels and exact quotes remain
  in the text/relationship records. Mark the abbreviation with an ellipsis.
- Diagram geometry follows a 4px grid. Focal treatment belongs only to the
  selected explanation; source evidence is neutral, regardless of confidence.
- On narrow screens, reflow text and controls; preserve readable SVG type in a
  local horizontal scroller. Printing removes controls and scales the figure.

Source of design principles:
[cathrynlavery/diagram-design](https://github.com/cathrynlavery/diagram-design),
v2.6, revision `f903933a534ba92cde1c85a28186267b3a317bb2`, MIT.
The integration does not vendor its templates or require its local install path.
