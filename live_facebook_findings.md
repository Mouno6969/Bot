# Live Facebook workflow findings

Tested public profile: https://www.facebook.com/sayeedmr

The authenticated Facebook profile page exposed the profile-level More options control with a menu containing four visible plain-text rows: Search, Report profile, Help Sayeed, and Block.

After selecting Report profile, Facebook opened a Report modal. The first modal contained ten plain-text reason rows: Problem involving someone under 18; Bullying, harassment or abuse; Suicide or self-harm; Violent, hateful or disturbing content; Selling or promoting restricted items; Adult content; Scam, fraud or false information; Fake profile; Intellectual property; Something else.

The browser accessibility index included duplicate modal container entries and other page controls. A global visible interactive-element selector is unsafe because it can count unrelated profile buttons and duplicate modal wrappers. During the live test, clicking accessibility element 53 selected Suicide or self-injury (the third reason) rather than the requested second reason, Bullying, harassment or abuse. The report modal was then closed without submission.

The implementation should scope each positional selection to the currently open menu/modal container and select only its direct visible text rows, not all visible buttons on the page.

## Nested flow observation

Selecting the second outer report reason, `Bullying, harassment or abuse`, opened a nested sheet titled `How is it bullying, harassment or abuse?` with four plain-text inner rows: `Threatening to share my nude images`, `Seems like sexual exploitation`, `Seems like human trafficking`, and `Bullying or harassment`.

Proposed command syntax: `/link <facebook_url> <outer_option> <inner_option>`, for example `/link https://www.facebook.com/sayeedmr 2 4` selects outer row 2 and then inner row 4. The implementation must remain scoped to the active report sheet and must stop before any final report submission.

## Nested live smoke test

The scoped live test on https://www.facebook.com/sayeedmr selected the outer menu row `Report profile`, then outer reason 2 `Bullying, harassment or abuse`, then inner row 4 `Bullying or harassment`. The nested flow was reached and selected correctly, with no report submitted.

## Deeper nesting observation

After selecting outer reason 2, then inner reason 4 (`Bullying or harassment`), Facebook opened another plain-text sheet titled `Who is being harassed?` with three rows: `Me`, `A friend`, and `I don't know them`.

This confirms the command should support a variable-length positional path, such as `/link <url> 2 4 1`, rather than limiting selection to only one outer and one inner number.
