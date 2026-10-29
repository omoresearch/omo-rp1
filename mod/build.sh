#!/usr/bin/env bash
# Build the Omo RP1 client mod with plain javac (Minecraft 26.2 ships unobfuscated, so no Gradle is needed).
#   ./build.sh                build only -> build/rp1-1.0.0+mc26.2.jar
#   ./build.sh install        build + copy into $MC_MODS (must be set explicitly; takes effect next launch)
#   ./build.sh test [URL]     build + run the standalone harness (test/) against a sidecar (default :8797)
#   ./build.sh voicetest [ws://127.0.0.1:8792/svc a.pcm ...]   PCM/Opus/config/focus tests (+ optional live backend)
#
# Paths (env vars; defaults match an Omo Space install, any Prism/MultiMC instance works):
#   JAVA_HOME / JDK_BIN   JDK 21+ (javac, jar, java)          default: ~/.local/share/omo/toolchain/jdk25, else PATH
#   PRISM                 Prism Launcher data dir (uses $PRISM/libraries)
#   MC_LIBRARIES          launcher libraries dir holding com/mojang/minecraft/26.2/minecraft-26.2-client.jar etc.
#   MC_MODS               the instance's mods/ dir (fabric-api-*.jar is read from here; install target)
#   FABRIC_API_JAR        explicit fabric-api jar (default: first $MC_MODS/fabric-api-*.jar)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; cd "$HERE"
OMO="$HOME/.local/share/omo"
if [ -n "${JDK_BIN:-}" ]; then J="$JDK_BIN"
elif [ -n "${JAVA_HOME:-}" ]; then J="$JAVA_HOME/bin"
else J="$OMO/toolchain/jdk25/bin"; fi
[ -x "$J/javac" ] || J="$(dirname "$(command -v javac)")"
if [ -n "${MC_LIBRARIES:-}" ]; then L="$MC_LIBRARIES"
elif [ -n "${PRISM:-}" ]; then L="$PRISM/libraries"
else L="$OMO/runtime/prism/libraries"; fi
MODS_READ="${MC_MODS:-$OMO/runtime/game/mods}"
FAPI="${FABRIC_API_JAR:-$(ls "$MODS_READ"/fabric-api-*.jar 2>/dev/null | head -1)}"
[ -f "$FAPI" ] || { echo "fabric-api jar not found: set FABRIC_API_JAR or MC_MODS" >&2; exit 1; }
[ -f "$L/com/mojang/minecraft/26.2/minecraft-26.2-client.jar" ] || {
  echo "minecraft-26.2-client.jar not under $L: set MC_LIBRARIES (or PRISM) to your launcher's libraries dir" >&2; exit 1; }
NAME=rp1-1.0.0+mc26.2.jar

# clean only our own build dir
[ -d "$HERE/build" ] && find "$HERE/build" -mindepth 1 -delete
mkdir -p build/fapi build/classes
"$J/jar" --extract --file "$FAPI" --dir build/fapi 'META-INF/jars/' 2>/dev/null || true
CP="$L/com/mojang/minecraft/26.2/minecraft-26.2-client.jar"
for f in build/fapi/META-INF/jars/*.jar \
         "$L"/net/fabricmc/fabric-loader/*/*.jar \
         "$L"/com/mojang/datafixerupper/*/*.jar \
         "$L"/com/mojang/brigadier/*/*.jar "$L"/com/mojang/logging/*/*.jar \
         "$L"/com/google/guava/guava/*/*.jar "$L"/com/google/code/gson/gson/*/*.jar \
         "$L"/org/joml/joml/*/*.jar "$L"/it/unimi/dsi/fastutil/*/*.jar \
         "$L"/org/slf4j/slf4j-api/*/*.jar "$L"/org/jspecify/jspecify/*/*.jar; do
  [ -f "$f" ] && CP="$CP:$f"
done
# Simple Voice Chat API: compile-only (SVC is optional at runtime; deps/fetch.py downloads + verifies it)
SVC_API="$(ls "$HERE"/deps/voicechat-api-*.jar 2>/dev/null | head -1)"
SVC_JAR="$(ls "$HERE"/deps/voicechat-fabric-*.jar 2>/dev/null | head -1)"
[ -n "$SVC_API" ] && CP="$CP:$SVC_API"
SRCS="$(find src/main/java -name '*.java')"
[ -n "$SVC_API" ] || { echo "deps/voicechat-api-*.jar missing (run deps/fetch.py): building without the voice chat plugin"; SRCS="$(echo "$SRCS" | grep -v Rp1VoicePlugin.java)"; }
"$J/javac" --release 21 -proc:none -Xlint:-options -cp "$CP" -d build/classes $SRCS
cp -r src/main/resources/. build/classes/
OUT="build/$NAME"
"$J/jar" --create --file "$OUT" -C build/classes .
echo "built $OUT ($(stat -c %s "$OUT") bytes)"

if [ "${1:-}" = test ]; then
  GSON="$(ls "$L"/com/google/code/gson/gson/*/*.jar | head -1)"
  mkdir -p build/test
  # the harness sees only the pure-Java classes (no Minecraft/Fabric on its classpath)
  "$J/javac" --release 21 -proc:none -cp "build/classes:$GSON" -d build/test $(find test -name '*.java')
  "$J/java" -cp "build/test:build/classes:$GSON" dev.yifan.rp1.Harness "${2:-http://127.0.0.1:8797}"
fi

if [ "${1:-}" = voicetest ]; then
  [ -n "$SVC_JAR" ] || { echo "voicetest needs deps/voicechat-fabric-*.jar: run python3 deps/fetch.py" >&2; exit 1; }
  GSON="$(ls "$L"/com/google/code/gson/gson/*/*.jar | head -1)"
  LOG4J="$(ls "$L"/org/apache/logging/log4j/log4j-api/*/*.jar | head -1):$(ls "$L"/org/apache/logging/log4j/log4j-core/*/*.jar | head -1)"
  mkdir -p build/test-voice
  "$J/javac" --release 21 -proc:none -cp "build/classes:$GSON:$SVC_JAR" -d build/test-voice $(find test-voice -name '*.java')
  "$J/java" -cp "build/test-voice:build/classes:$GSON:$SVC_JAR:$LOG4J" dev.yifan.rp1.VoiceTests "${@:2}"
fi

if [ "${1:-}" = install ]; then
  [ -n "${MC_MODS:-}" ] || { echo "install: set MC_MODS to your instance's mods/ dir" >&2; exit 1; }
  BACKUPS="${MC_BACKUPS:-$(dirname "$MC_MODS")/rp1-replaced-jars}"
  # only older rp1-*.jar files are moved aside (outside mods/, so nothing loads twice); other mods are never touched
  for old in "$MC_MODS"/rp1-*.jar; do
    if [ -e "$old" ] && [ "$(basename "$old")" != "$NAME" ]; then
      mkdir -p "$BACKUPS"
      mv "$old" "$BACKUPS/$(basename "$old").replaced"
    fi
  done
  cp "$OUT" "$MC_MODS/.rp1.tmp" && mv "$MC_MODS/.rp1.tmp" "$MC_MODS/$NAME"
  sha256sum "$MC_MODS/$NAME"
fi
