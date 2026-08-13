// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Derived from the initialiser that `mdbook-mermaid install` generates
// (mdbook-mermaid 0.17.0, MPL-2.0). `make build-docs` copies this file over
// the generated one before building. Two reasons it exists:
//
//   1. The generated initialiser is written against mdBook 0.5.0, which
//      gave the theme buttons bare ids (`coal`, `ayu`, ...). mdBook 0.5.2
//      renamed them to `mdbook-theme-coal` and so on, so the generated
//      code calls `.addEventListener` on null and throws on every page.
//      Diagrams still render, because `mermaid.initialize` runs first, but
//      switching theme leaves them in the old palette. This is the
//      concrete cost of the version skew recorded in
//      `scripts/cargo-tools.lock`.
//
//   2. It hardcodes mermaid's stock `default` and `dark` palettes, which
//      do not match the Quip theme.
//
// Every lookup below tolerates a missing element, so a future mdBook
// markup change degrades to "diagrams do not re-render on theme switch"
// rather than to a thrown error.

(() => {
    // Theme ids that `docs/book/theme/index.hbs` can produce. `navy` and
    // `ayu` are no longer offered by the picker, but a reader who used
    // them before the picker was trimmed still has the value in
    // localStorage, so keep recognising them.
    const darkThemes = ['coal', 'navy', 'ayu'];
    const lightThemes = ['light', 'rust'];

    const classList = document.getElementsByTagName('html')[0].classList;

    let isLight = true;
    for (const cssClass of classList) {
        if (darkThemes.includes(cssClass)) {
            isLight = false;
            break;
        }
    }

    // Shared across both palettes. Mermaid's `base` theme derives most of
    // its colours from these, which is why we use it rather than patching
    // `default` and `dark`.
    const common = {
        fontFamily: 'var(--mono-font)',
        fontSize: '14px',
    };

    const light = {
        background: '#F2F2F2',
        primaryColor: '#F3FEFF',
        primaryBorderColor: '#4CE0FF',
        primaryTextColor: '#1A1A1A',
        secondaryColor: '#EEFDCA',
        tertiaryColor: '#FFFCDA',
        lineColor: '#525252',
        textColor: '#1A1A1A',
        mainBkg: '#FFFFFF',
        nodeBorder: '#4CE0FF',
        clusterBkg: '#FFFFFF',
        clusterBorder: '#DCDCDC',
        edgeLabelBackground: '#F2F2F2',
    };

    const dark = {
        darkMode: true,
        background: '#1A1A1A',
        primaryColor: '#282828',
        primaryBorderColor: '#4CE0FF',
        primaryTextColor: '#F2F2F2',
        secondaryColor: '#1F1F1F',
        tertiaryColor: '#333333',
        lineColor: '#A9A9A9',
        textColor: '#F2F2F2',
        mainBkg: '#282828',
        nodeBorder: '#4CE0FF',
        clusterBkg: '#1F1F1F',
        clusterBorder: '#333333',
        edgeLabelBackground: '#1A1A1A',
    };

    mermaid.initialize({
        startOnLoad: true,
        theme: 'base',
        themeVariables: Object.assign({}, common, isLight ? light : dark),
    });

    // Mermaid has no supported way to restyle diagrams that are already
    // drawn, so the upstream initialiser reloads the page on a theme
    // change that crosses the light/dark line. Keep that behaviour, but
    // bind to the ids mdBook 0.5.2 actually emits.
    const onThemeClick = (themeId, becomesLight) => {
        const button = document.getElementById(`mdbook-theme-${themeId}`);
        if (button === null) {
            return;
        }
        button.addEventListener('click', () => {
            if (becomesLight !== isLight) {
                window.location.reload();
            }
        });
    };

    darkThemes.forEach((themeId) => onThemeClick(themeId, false));
    lightThemes.forEach((themeId) => onThemeClick(themeId, true));

    // "Auto" resolves through the OS setting rather than to a fixed
    // palette, so compare against that rather than against a theme id.
    const auto = document.getElementById('mdbook-theme-default_theme');
    if (auto !== null) {
        auto.addEventListener('click', () => {
            const prefersDark = window.matchMedia(
                '(prefers-color-scheme: dark)',
            ).matches;
            if (prefersDark === isLight) {
                window.location.reload();
            }
        });
    }
})();
