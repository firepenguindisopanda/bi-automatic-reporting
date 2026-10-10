#!/usr/bin/env bash
# Enforces the visual rules the design direction is built on.
#
# These are easy to reintroduce by habit - a component library snippet, a
# generated style - so they are checked rather than trusted.
#
#   scripts/check-design-rules.sh              check the repository
#   scripts/check-design-rules.sh --self-test  prove every rule still bites
#
# Plain bash + GNU grep + POSIX awk (mawk on Ubuntu is fine): no other tools.
set -uo pipefail

cd "$(dirname "$0")/.."

CODE=(--include='*.ts' --include='*.tsx' --include='*.js' --include='*.jsx')
STYLE=(--include='*.css')

# Tailwind's side and corner radius variants: rounded-t-*, rounded-tl-*, ...
SIDES='(t|r|b|l|s|e|tl|tr|br|bl|ss|se|es|ee)'

# 3, 4, 6 or 8 hex digits. Not after & (so &#8217; entities pass) or a word
# character, and not followed by one (so #feedback is not read as #feed).
HEX='(^|[^&0-9A-Za-z_])#([0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b'
COLOUR_FN='\b(rgba?|hsla?|hwb|oklch|oklab|lch|lab)\('

# Reads CSS and strips comments. A design token is a custom property
# (--name: value) declared in a block whose selector starts with :root or
# @theme - frontend/src/index.css - the one place raw values belong.
#   mode=plain   print every line that is not a token as path:line:text
#   mode=radius  print radius declarations as path:line:decl - border-*radius
#                outside tokens, plus every --radius* token, so the scale
#                itself cannot drift
# shellcheck disable=SC2016
CSS_SCAN='
function trim(s) { gsub(/^[ \t\r]+|[ \t\r]+$/, "", s); return s }
FNR == 1 { depth = 0; incomment = 0; buf = ""; intokdecl = 0 }
{
  sub(/\r$/, "")
  line = $0; text = ""
  while (line != "") {
    if (incomment) {
      p = index(line, "*/")
      if (p == 0) { line = "" } else { line = substr(line, p + 2); incomment = 0 }
    } else {
      p = index(line, "/*")
      if (p == 0) { text = text line; line = "" }
      else { text = text substr(line, 1, p - 1); line = substr(line, p + 2); incomment = 1 }
    }
  }

  istoken = intokdecl
  if (!istoken && depth > 0 && tok[depth] && text ~ /^[ \t]*--[A-Za-z0-9_-]+[ \t]*:/) istoken = 1
  intokdecl = (istoken && index(text, ";") == 0 && index(text, "}") == 0)

  for (i = 1; i <= length(text); i++) {
    c = substr(text, i, 1)
    if (c == "{") { depth++; tok[depth] = (trim(buf) ~ /^(:root|@theme)/); buf = "" }
    else if (c == "}") { if (depth > 0) depth--; buf = "" }
    else if (c == ";") { buf = "" }
    else { buf = buf c }
  }

  where = FILENAME ":" FNR ":"
  if (mode == "plain") {
    if (!istoken) print where text
    next
  }
  rest = text
  while (match(rest, /(border(-[a-z]+)*-radius|--radius[A-Za-z0-9_-]*)[ \t]*:[^;}]*/)) {
    decl = substr(rest, RSTART, RLENGTH)
    rest = substr(rest, RSTART + RLENGTH)
    if (!istoken || decl ~ /^--radius/) print where trim(decl)
  }
}'

# Reads path:line:snippet, where the snippet is rounded-[...] or a
# "property: value" declaration, and prints the ones whose radius is not
# 4-6px. 0 (square), var()/tokens and 50% (circles, see below) are allowed.
# shellcheck disable=SC2016
RADIUS_EVAL='
function in_scale(t,   n) {
  gsub(/^[ \t'"'"'"`]+|[ \t'"'"'"`;]+$/, "", t)
  if (t == "" || t == "!important") return 1
  if (t ~ /^(var\(|--)/) return 1
  if (t ~ /^(inherit|initial|unset|revert|revert-layer)$/) return 1
  gsub(/\)+$/, "", t)
  if (t == "50%") return 1
  if (t ~ /^0*\.?0+(px|r?em|%)?$/) return 1
  if (t ~ /^[0-9]*\.?[0-9]+(px)?$/) { n = t + 0; return n >= 4 && n <= 6 }
  if (t ~ /^[0-9]*\.?[0-9]+r?em$/) { n = (t + 0) * 16; return n >= 4 && n <= 6 }
  return 0
}
{
  sub(/\r$/, "")
  i = index($0, ":"); rest = substr($0, i + 1)
  j = index(rest, ":")
  where = substr($0, 1, i + j); snippet = substr(rest, j + 1)
  if (snippet ~ /^rounded/) {
    value = substr(snippet, index(snippet, "[") + 1)
    sub(/\]$/, "", value)
    gsub(/_/, " ", value)
  } else {
    value = substr(snippet, index(snippet, ":") + 1)
  }
  n = split(value, parts, "[ \t/,]+")
  for (k = 1; k <= n; k++) {
    if (!in_scale(parts[k])) { print where " " snippet; next }
  }
}'

status=0

fail() {
  echo "FAIL: $1"
  status=1
}

# report MESSAGE OK-MESSAGE HITS
report() {
  if [ -n "$3" ]; then
    fail "$1"
    echo "$3" | sed 's/^/    /'
  else
    echo "  ok  $2"
  fi
}

# Runs every check over a source tree and an assets directory; anything after
# those two is a server-side template that must stay gradient-free too.
run_checks() {
  local src=$1 public=$2
  shift 2
  local extra=("$@")
  local hits css_files=()
  status=0

  while IFS= read -r f; do css_files+=("$f"); done < <(find "$src" -type f -name '*.css' 2>/dev/null)

  # 1a. No CSS gradients. Depth comes from hairline rules and the paper/surface
  #     step. Tailwind v4 spells them bg-linear-*, bg-radial-*, bg-conic-*
  #     (and mask-* for gradient masks); v3 spelt them bg-gradient-*.
  hits=$(grep -rnIiE \
    'linear-gradient|radial-gradient|conic-gradient|bg-gradient-|\b(bg|mask)-(linear|radial|conic)\b' \
    "$src" "${extra[@]}" 2>/dev/null)
  report "CSS gradients are not used in this design" "no CSS gradients" "$hits"

  # 1b. No gradients in SVG either - in asset files or inline in components. A
  #     blurred stack of coloured shapes reads as a gradient even though it
  #     never uses the gradient functions above - which is exactly how the old
  #     template favicon slipped past this check.
  hits=$(grep -rnIiE '<(linear|radial)Gradient|feGaussianBlur|stop-?color' \
    "$public" "$src" --include='*.svg' "${CODE[@]}" 2>/dev/null)
  report "gradient or blur effects in SVG" "no gradient effects in SVG" "$hits"

  # 2. Radii stay at 4-6px: rounded-sm (4px), rounded-md / rounded-card (6px).
  #    rounded-xs (2px) is too sharp; rounded-lg and up, on any side or
  #    corner, are too soft.
  hits=$(grep -rnIE "\\brounded(-$SIDES)?-(xs|lg|xl|2xl|3xl|4xl)\\b" \
    "$src" "${CODE[@]}" "${STYLE[@]}" 2>/dev/null)
  #    Arbitrary values, raw CSS (outside the tokens), inline styles and the
  #    --radius* tokens themselves must land in 4-6px too.
  hits+=$'\n'$( {
    grep -rnoIE "\\brounded(-$SIDES)?-\\[[^]]*\\]" "$src" "${CODE[@]}" "${STYLE[@]}" 2>/dev/null
    grep -rnoIE "borderRadius['\"]?[[:space:]]*:[[:space:]]*[^,}]+" "$src" "${CODE[@]}" 2>/dev/null
    grep -rnoIE 'border(-[a-z]+)*-radius[[:space:]]*:[^;}"`]+' "$src" "${CODE[@]}" 2>/dev/null
    [ ${#css_files[@]} -gt 0 ] && awk -v mode=radius "$CSS_SCAN" "${css_files[@]}"
  } | awk "$RADIUS_EVAL")
  #    Allowlisted on purpose: rounded-full (or border-radius: 50%) is how the
  #    codebase draws circular status dots, e.g. the running-stage Spinner.
  #    It is only a circle on a square element, so it must sit with a size-*
  #    utility; anywhere else it is a pill. Side variants are never circles.
  hits+=$'\n'$( {
    grep -rnIE "\\brounded-$SIDES-full\\b" "$src" "${CODE[@]}" "${STYLE[@]}" 2>/dev/null
    grep -rnIE '\brounded-full\b' "$src" "${CODE[@]}" "${STYLE[@]}" 2>/dev/null |
      grep -vE '\bsize-([0-9]|\[|\(|px\b)'
  } )
  hits=$(printf '%s\n' "$hits" | sed '/^[[:space:]]*$/d' | sort -u)
  report "border radii outside 4-6px (rounded-full is only for size-* circles)" \
    "radii within scale (rounded-full allowed on size-* circles)" "$hits"

  # 3. Only the two defined shadows. Arbitrary shadow utilities drift.
  hits=$(grep -rnIE 'shadow-(2xs|xs|sm|md|lg|xl|2xl|inner)\b|shadow-\[' \
    "$src" "${CODE[@]}" "${STYLE[@]}" 2>/dev/null)
  report "use shadow-card or shadow-lift, not Tailwind's default scale" "shadows within scale" "$hits"

  # 4. Colours come from tokens, so light and dark stay in step. Literals in
  #    code - hex, rgb(), hsl(), oklch()... - bypass that entirely, and so do
  #    literals in CSS anywhere but the token definitions.
  hits=$( {
    grep -rnIE "$HEX|$COLOUR_FN" "$src" "${CODE[@]}" 2>/dev/null
    [ ${#css_files[@]} -gt 0 ] && awk -v mode=plain "$CSS_SCAN" "${css_files[@]}" |
      grep -E "$HEX|$COLOUR_FN"
  } )
  report "hard-coded colours - use a token from frontend/src/index.css" "colours come from tokens" "$hits"

  return $status
}

# Writes one fixture per rule, runs the checks over them, and fails unless
# every banned line is reported and every allowed pattern passes clean.
self_test() {
  local tmp out rc missing=0 expected=0
  tmp=$(mktemp -d)
  SELF_TEST_DIR=$tmp
  trap 'rm -rf -- "$SELF_TEST_DIR"' EXIT
  mkdir -p "$tmp/bad/src" "$tmp/bad/public" "$tmp/good/src" "$tmp/good/public"

  # Every line naming a ban_* marker must be reported.
  cat > "$tmp/bad/src/Bad.tsx" <<'EOF'
const ban_bg_gradient = 'bg-gradient-to-r from-accent'
const ban_gradient_fn = { background: 'linear-gradient(red, blue)' }
const ban_bg_linear = 'bg-linear-to-r'
const ban_bg_linear_angle = 'hover:bg-linear-45'
const ban_bg_radial = 'bg-radial'
const ban_bg_conic = 'bg-conic-180'
const ban_mask_linear = 'mask-linear-from-50%'
const ban_svg_linear = <linearGradient id="g" />
const ban_svg_radial = <radialGradient id="r" />
const ban_svg_stop = <stop stopColor="currentColor" />
const ban_rounded_lg = 'rounded-lg'
const ban_rounded_xl = 'rounded-xl'
const ban_rounded_4xl = 'rounded-4xl'
const ban_rounded_xs = 'rounded-xs'
const ban_rounded_t_lg = 'rounded-t-lg'
const ban_rounded_tl_xl = 'focus:rounded-tl-xl'
const ban_rounded_s_2xl = 'rounded-s-2xl'
const ban_rounded_px12 = 'rounded-[12px]'
const ban_rounded_px1 = 'rounded-[1px]'
const ban_rounded_rem = 'rounded-[0.75rem]'
const ban_rounded_b_arbitrary = 'rounded-b-[10px]'
const ban_rounded_full_pill = 'rounded-full px-4'
const ban_rounded_t_full = 'size-3 rounded-t-full'
const ban_style_radius = { borderRadius: 12 }
const ban_style_radius_str = { borderRadius: '2px' }
const ban_shadow_lg = 'shadow-lg'
const ban_shadow_arbitrary = 'shadow-[0_0_4px_black]'
const ban_hex3 = '#fff'
const ban_hex4 = '#ffff'
const ban_hex6 = 'bg-[#1a1815]'
const ban_hex8 = '#1a1815cc'
const ban_rgb = 'rgb(0 0 0)'
const ban_rgba = 'rgba(0, 0, 0, 0.5)'
const ban_hsl = 'hsl(10 50% 50%)'
const ban_oklch = 'oklch(0.7 0.1 30)'
EOF
  cat > "$tmp/bad/src/colours.ts" <<'EOF'
export const ban_ts_hex = '#abc'
export const ban_ts_rgb = 'rgb(1, 2, 3)'
EOF
  cat > "$tmp/bad/src/bad.css" <<'EOF'
.ban-css-gradient { background: linear-gradient(red, blue); }
.ban-css-radius-8 { border-radius: 8px; }
.ban-css-radius-2 { border-radius: 2px; }
.ban-css-radius-corner { border-top-left-radius: 12px; }
.ban-css-radius-pill { border-radius: 9999px; }
.ban-css-radius-rem { border-radius: 1rem; }
.ban-css-apply { @apply rounded-xl; }
.ban-css-hex { color: #fff; }
.ban-css-rgb { color: rgb(0 0 0 / 0.5); }
.ban-css-hsla { color: hsla(0, 0%, 0%, 0.5); }
.ban-css-oklch { color: oklch(0.7 0.1 30); }
:root {
  --paper: #fbfaf7;
  quotes: "ban-css-root-non-token"; color: #000;
  --radius-ban-token-pill: 9999px;
}
EOF
  cat > "$tmp/bad/public/bad.svg" <<'EOF'
<svg xmlns="http://www.w3.org/2000/svg">
<linearGradient id="ban-svg-asset-gradient" />
<filter><feGaussianBlur id="ban-svg-asset-blur" /></filter>
</svg>
EOF

  # None of this may be reported.
  cat > "$tmp/good/src/Good.tsx" <<'EOF'
const radii = 'rounded-sm rounded-md rounded rounded-card rounded-none rounded-t-sm rounded-b-md'
const arbitrary = 'rounded-[4px] rounded-[6px] rounded-[0.25rem] rounded-[var(--radius)] rounded-(--radius)'
const dot = <span className="inline-block size-3 animate-pulse rounded-full bg-accent" />
const style = { borderRadius: 4, padding: 0 }
const skip = <a href="#main">Skip to content</a>
const entity = <p>Don&#8217;t, #feedback</p>
const shadows = 'shadow-card shadow-lift'
const colours = 'bg-accent text-ink border-rule'
EOF
  cat > "$tmp/good/src/good.css" <<'EOF'
:root {
  --paper: #fbfaf7;
  --shadow: 0 1px 2px rgb(26 24 21 / 0.06);
  --radius-sm: 4px;
  --font-serif: "Source Serif 4",
    Georgia, serif;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --paper: #14130f;
    --accent: oklch(0.7 0.1 30);
  }
}
@theme inline {
  --color-paper: var(--paper);
  --radius-card: 6px;
}
/* Comments may mention #fff, rgb(0 0 0) or border-radius: 12px freely. */
#main .dot { border-radius: 50%; color: var(--ink); }
.card { border-radius: var(--radius-sm); }
.box { border-radius: 4px 6px; outline: 2px solid var(--accent); }
.flat { border-radius: 0; }
EOF
  cat > "$tmp/good/public/icon.svg" <<'EOF'
<svg xmlns="http://www.w3.org/2000/svg"><rect fill="#1a1815" rx="2" /></svg>
EOF

  echo "Self-test: banned patterns"
  out=$(run_checks "$tmp/bad/src" "$tmp/bad/public")
  rc=$?
  while IFS=: read -r file line marker; do
    expected=$((expected + 1))
    if printf '%s\n' "$out" | grep -qF -- "$file:$line:"; then
      echo "  caught  $marker"
    else
      echo "  MISSED  $marker ($file:$line)"
      missing=$((missing + 1))
    fi
  done < <(grep -rnoE 'ban[_-][A-Za-z0-9_-]*[A-Za-z0-9]' "$tmp/bad")

  echo "Self-test: allowed patterns"
  local good_out good_rc
  good_out=$(run_checks "$tmp/good/src" "$tmp/good/public")
  good_rc=$?
  if [ "$good_rc" -eq 0 ]; then
    echo "  ok  nothing reported"
  else
    echo "  FALSE POSITIVES:"
    printf '%s\n' "$good_out" | grep -v '^  ok' | sed 's/^/    /'
  fi

  if [ "$rc" -ne 0 ] && [ "$missing" -eq 0 ] && [ "$good_rc" -eq 0 ]; then
    echo "Self-test passed: $expected banned patterns caught, allowed patterns clean."
    return 0
  fi
  echo "Self-test FAILED: $missing of $expected banned patterns missed; allowed-pattern check exit $good_rc."
  return 1
}

if [ "${1:-}" = "--self-test" ]; then
  self_test
  exit $?
fi

echo "Checking design rules..."
run_checks frontend/src frontend/public app/report/generator.py app/email/templates.py
exit $?
