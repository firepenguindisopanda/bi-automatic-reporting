#!/usr/bin/env bash
# Enforces the visual rules the design direction is built on.
#
# These are easy to reintroduce by habit - a component library snippet, a
# generated style - so they are checked rather than trusted.
set -uo pipefail

cd "$(dirname "$0")/.."
status=0

fail() {
  echo "FAIL: $1"
  status=1
}

echo "Checking design rules..."

# 1a. No CSS gradients. Depth comes from hairline rules and the paper/surface step.
targets=(frontend/src app/report/generator.py app/email/templates.py)
if hits=$(grep -rniE "linear-gradient|radial-gradient|conic-gradient|bg-gradient-" "${targets[@]}" 2>/dev/null); then
  fail "CSS gradients are not used in this design"
  echo "$hits" | sed 's/^/    /'
else
  echo "  ok  no CSS gradients"
fi

# 1b. No gradients in assets either. A blurred stack of coloured shapes reads
#     as a gradient even though it never uses the gradient functions above -
#     which is exactly how the old template favicon slipped past this check.
if hits=$(grep -rlniE "<linearGradient|<radialGradient|feGaussianBlur|stop-color" \
    frontend/public frontend/src --include="*.svg" 2>/dev/null); then
  fail "gradient or blur effects in SVG assets"
  echo "$hits" | sed 's/^/    /'
else
  echo "  ok  no gradient effects in assets"
fi

# 2. Radii stay small. Tailwind's rounded-lg and above are out of scale here;
#    rounded-full is fine for status dots.
if hits=$(grep -rnE "rounded-(lg|xl|2xl|3xl)\b" frontend/src 2>/dev/null); then
  fail "border radii larger than 6px"
  echo "$hits" | sed 's/^/    /'
else
  echo "  ok  radii within scale"
fi

# 3. Only the two defined shadows. Arbitrary shadow utilities drift.
if hits=$(grep -rnE "shadow-(sm|md|lg|xl|2xl)\b" frontend/src 2>/dev/null); then
  fail "use shadow-card or shadow-lift, not Tailwind's default scale"
  echo "$hits" | sed 's/^/    /'
else
  echo "  ok  shadows within scale"
fi

# 4. Colours come from tokens, so light and dark stay in step. Hex literals in
#    components bypass that entirely.
if hits=$(grep -rnE "#[0-9a-fA-F]{6}\b" frontend/src --include="*.tsx" 2>/dev/null); then
  fail "hard-coded colours in components - use a token"
  echo "$hits" | sed 's/^/    /'
else
  echo "  ok  colours come from tokens"
fi

exit $status
