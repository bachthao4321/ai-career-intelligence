# CareerLens — job board interface

Current direction follows the user's request for an interface that feels like a
working recruitment product rather than an AI landing-page template. Use the
installed UI UX Pro Max minimalism guidance selectively for hierarchy, restrained
color and functional layout. Do not use the generator's conversion funnel.

## Layout and copy

- Keep navy navigation and cobalt primary actions, as requested in earlier color
  feedback. Use neutral gray canvas and white content rather than blue decoration.
- Start with “Việc làm IT”, a short explanation and a query/location search row.
  Put source/workplace/skill filters beside results above 1000 px; use a native
  collapsible panel below that width, with applied chips remaining visible.
- Show dataset counts as compact metadata, not oversized marketing statistics.
- Render jobs as one list of white rows separated by rules. Prioritize company,
  title, location, source and skills. Use a short excerpt; full text stays in detail.
- Keep the supporting skill statistics plain. CV entry is a small functional link
  with a description, not a promotional card.
- Remove slogans, decorative arrows, the “new” badge and the simulated user avatar.
  Navigation and analytics/CV headings should describe what the tool does.
- Keep source restrictions, evidence, actual status indicators and privacy notices.
- Supporting statistics and the CV entry follow the list. Mobile uses a single
  content column. The
  native job detail dialog remains keyboard accessible and retains source evidence.

## Palette

| Role | Color | Foreground |
| --- | --- | --- |
| Primary action | `#1D4ED8` | `#FFFFFF` |
| Navigation | `#10264D` | `#E4EDFF` |
| Canvas | `#F3F4F6` | `#172235` |
| Content | `#FFFFFF` | `#172235` |
| Muted surface | `#E7EDF6` | `#334155` |
| Divider | `#C0C9D6` | — |
| Control border | `#647B9A` | — |
| Destructive action | `#DC2626` | `#FFFFFF` |

Use semantic tokens in `app/web/static/blue.css`. Legacy green/sage variable names
are compatibility aliases. Meaningful states also use text; never rely on color
alone. Preserve visible keyboard focus, including light focus rings on navy.

## Typography and controls

- Self-hosted Open Sans for body and complete Vietnamese headings, weights 400–700.
- Poppins SemiBold for the English brand. A browser probe confirmed that Poppins
  alone needs fallback for Vietnamese diacritics; avoid mixed faces within a word.
- Fonts, Vietnamese/Latin subsets and OFL licenses live in `/assets/fonts/`.
  No runtime Google Fonts request or CSP expansion is needed.
- No shadows or gradients. Use 3–4 px corners, clear separators and restrained
  icons. Controls retain comfortable targets, visible labels and wrapping chips.
- Color transitions respect reduced motion. Explicit search and pagination focus
  the result heading below sticky navigation; filter changes retain control focus.

## Local verification

Chrome checked 320, 375, 768, 1024 and 1440 px, plus 812×375 landscape: no horizontal
page overflow. At 375 px and 200% root text size, no horizontal overflow.

Search, source filter, removing a condition, reset, empty state, pagination, rapid
filter changes and query-to-submit Tab order passed. Detail opens, closes with
Escape and does not overflow horizontally on mobile. Sample analytics returned
the dataset count. A synthetic PDF returned two skills and five matches; its
stored profile was deleted through the UI after the check. No model calls needed.

Seven sampled text/background pairs measured 6.61:1–15.95:1, including 6.70:1 for
the primary button. No uncaught JavaScript exceptions observed. The platform shell
Python test and three existing Node logic tests passed; JavaScript syntax checked.

Screenshots remain ignored under `data/evaluations/ui-blue/`. Browser checks do
not constitute a complete accessibility audit. Physical-device, Safari and
Firefox testing remain unperformed. Dark mode is not enabled.

This iteration stays local. No commit or push was performed.

The research, source links, adaptation decisions and filter interaction checks
are documented in [the reference study](../../docs/DESIGN_REFERENCES.md).
