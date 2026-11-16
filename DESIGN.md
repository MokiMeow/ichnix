# Ichnix interface

The landing page, operator manual and review console share a dark desktop and mobile interface. The starting reference is [OpenCode’s site design guide](https://github.com/anomalyco/opencode/blob/dev/services/site/DESIGN.md). Ichnix owns its mark, typography, layout and review examples.

## Type and color

- Use Instrument Sans for the illustrated landing page headlines. Use IBM Plex Mono for manual and console headings, navigation, identifiers, commands and tabular case data. Use Instrument Sans for explanations and long-form instructions. Both fonts and their notices ship with the app.
- Use the tokens in [styles.css](public/styles.css) for text, accents, surfaces and rules. Keep large reading surfaces charcoal. Mark an observation with a word as well as a color. Display an absent setting in an evaluated manifest as inconclusive. When a check has no observation in a run, show “not recorded”.
- Keep the logo transparent, at its native proportions. Use square controls and thin dividers. Reserve the blue accent for links, selection and focus.

## Layout and interaction

- Give each page one primary heading. Use the shared masthead for the home link, docs and console. The landing page connects an illustrated review workflow to setup guides for each team. Keep each chapter number consistent with its manual heading and contents entry. The original engraved landscape is shared by the hero and landing-page footer. Center the workflow beneath its introduction. Center the private intake form and show file actions below the drop area. Let the case queue become a selector and show the current chapter in the collapsible manual contents.
- Label the landing workflow as an example. Describe what each stage does and link to the review instructions. Private case data belongs in the authenticated console.
- Preserve native links, forms, disclosures and keyboard behavior. Give each interactive item a visible focus indicator. Reading the landing example needs neither JavaScript nor credentials.
- Check the landing page, manual, public console and private workspace at 320px, an intermediate width and a wide desktop. Exercise the workflow stages by keyboard, open a manual section, select a case and check the reader’s available actions. Scan for overflow and verify that fonts and images resolve on the running server.
