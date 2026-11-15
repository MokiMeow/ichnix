# Ichnix interface

The landing page, operator manual and review console share a dark desktop and mobile interface. The starting reference is [OpenCode’s site design guide](https://github.com/anomalyco/opencode/blob/dev/services/site/DESIGN.md). Ichnix owns its mark, typography, layout and review examples.

## Type and color

- Use Instrument Sans for the illustrated landing page headlines. Use IBM Plex Mono for manual and console headings, navigation, identifiers, commands and tabular case data. Use Instrument Sans for explanations and long-form instructions. Both fonts and their notices ship with the app.
- Use the tokens in [styles.css](public/styles.css) for text, accents, surfaces and rules. Keep large reading surfaces charcoal. Mark an observation with a word as well as a color. A missing observation stays inconclusive.
- Keep the logo transparent, at its native proportions. Use square controls and thin dividers. Reserve the blue accent for links, selection and focus.

## Layout and interaction

- Give each page one primary heading. Use the shared masthead for the home link, docs and console. The landing page follows a sample case into chapter links for each team. Keep each chapter number consistent with its manual heading and contents entry. The original engraved landscape is shared by the hero and site footer. On small screens, stack the case inspector beneath the introduction. Let the case queue become a selector and keep the manual contents collapsible.
- Use real specimen data for the landing inspector. Keep the fixture label visible, and link to the instructions that reproduce the results. Private case data belongs in the authenticated console.
- Preserve native links, forms, disclosures and keyboard behavior. Give each interactive item a visible focus indicator. Reading the landing example needs neither JavaScript nor credentials.
- Check the three routes at 320px, an intermediate width and a wide desktop. Exercise the inspector by keyboard, open a manual section, select a case and check the reader’s available actions. Scan for overflow and verify that fonts and images resolve on the running server.
