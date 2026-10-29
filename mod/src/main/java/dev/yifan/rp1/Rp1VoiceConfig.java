package dev.yifan.rp1;

import java.io.IOException;
import java.io.Reader;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Properties;

/** config/rp1-voice.properties: the Simple Voice Chat bridge to Roboto's voice backend (bring your own; not included in this repo). Pure Java. */
public final class Rp1VoiceConfig {
    public static final String DEFAULT_URL = "ws://127.0.0.1:8792/svc";
    public volatile boolean enabled = true;
    /** Voice backend (not included in this repo). Loopback only. */
    public volatile String url = DEFAULT_URL;
    /** Only the local player's mic within this many blocks of his head is considered; 0 = always (any distance/dimension). */
    public volatile double hearRange = 12.0;
    /** Optional player name to listen to instead of the local (host) player. */
    public volatile String player = "";
    /** How far his voice carries (SVC locational channel distance, blocks). */
    public volatile float voiceDistance = 24f;
    /** Show what he says (and what he heard) in chat. */
    public volatile boolean captions = true;
    /** Body from /model.json that carries his head (the torso + head shell on the RP1). Missing: base_link + 0.5 m. */
    public volatile String headBody = "waist_yaw_link";
    /** Head centre above the head body's origin, along its local z, in sim metres (the shell tops out at ~0.42). */
    public volatile double headUp = 0.33;
    /** Looking within this many degrees of his head grabs mic focus (when also within hear_range). */
    public volatile double lookAngle = 30.0;

    public static Rp1VoiceConfig load(Path file) {
        Rp1VoiceConfig c = new Rp1VoiceConfig();
        if (!Files.exists(file)) {
            return c;
        }
        Properties p = new Properties();
        try (Reader r = Files.newBufferedReader(file, StandardCharsets.UTF_8)) {
            p.load(r);
        } catch (IOException e) {
            return c;
        }
        c.enabled = !"false".equalsIgnoreCase(p.getProperty("enabled", "true").trim());
        String u = p.getProperty("url", c.url).trim();
        if (u.startsWith("ws://127.0.0.1:") || u.startsWith("ws://localhost:")) {
            c.url = u; // loopback only: this carries the player's microphone
        }
        String range = p.getProperty("hear_range", "12").trim();
        c.hearRange = "always".equalsIgnoreCase(range) ? 0 : clamp(parse(range, 12), 0, 256);
        c.player = p.getProperty("player", "").trim();
        c.voiceDistance = (float) clamp(parse(p.getProperty("voice_distance", "24"), 24), 4, 128);
        c.captions = !"false".equalsIgnoreCase(p.getProperty("captions", "true").trim());
        String hb = p.getProperty("head_body", c.headBody).trim();
        c.headBody = hb.isEmpty() ? "waist_yaw_link" : hb;
        c.headUp = clamp(parse(p.getProperty("head_up", "0.33"), 0.33), -2, 2);
        c.lookAngle = clamp(parse(p.getProperty("look_angle", "30"), 30), 1, 180);
        return c;
    }

    public void save(Path file) throws IOException {
        Properties p = new Properties();
        p.setProperty("enabled", Boolean.toString(enabled));
        p.setProperty("url", url);
        p.setProperty("hear_range", hearRange <= 0 ? "always" : Double.toString(hearRange));
        p.setProperty("player", player);
        p.setProperty("voice_distance", Float.toString(voiceDistance));
        p.setProperty("captions", Boolean.toString(captions));
        p.setProperty("head_body", headBody);
        p.setProperty("head_up", Double.toString(headUp));
        p.setProperty("look_angle", Double.toString(lookAngle));
        Files.createDirectories(file.toAbsolutePath().getParent());
        try (Writer w = Files.newBufferedWriter(file, StandardCharsets.UTF_8)) {
            p.store(w, "RP1 voice 'Roboto' (Simple Voice Chat push-to-talk while looking at the RP1 or standing nearest to it ->"
                    + " GPT-Live persona; reply plays from his head). hear_range: blocks or 'always'. player: blank = you."
                    + " head_body: /model.json body name; head_up: sim metres above it.");
        }
    }

    /** Whether a mic packet from a player at distance {@code dist} (blocks, NaN = other dimension) is in range. */
    public boolean inRange(double dist) {
        if (hearRange <= 0) {
            return true;
        }
        return !Double.isNaN(dist) && dist <= hearRange;
    }

    private static double parse(String s, double def) {
        try {
            double v = Double.parseDouble(s.trim());
            return Double.isFinite(v) ? v : def;
        } catch (RuntimeException e) {
            return def;
        }
    }

    private static double clamp(double v, double lo, double hi) {
        return Math.max(lo, Math.min(hi, v));
    }
}
