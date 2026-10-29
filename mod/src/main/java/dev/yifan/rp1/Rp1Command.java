package dev.yifan.rp1;

import java.util.Locale;

/** Parser for `!rp1 ...` chat lines. Pure Java. */
public final class Rp1Command {

    public enum Kind {
        /** Not a !rp1 message: let it through (to other mods / the server). */
        NOT_RP1,
        HELP,
        /** Forward to the sidecar: {@link #json} is the POST /cmd body. */
        SIDECAR,
        HERE,
        SCALE,
        /** Bad usage; {@link #message} explains. */
        ERROR
    }

    public static final String PREFIX = "!rp1";
    public static final double MIN_SCALE = 0.25;
    public static final double MAX_SCALE = 10.0;

    public static final String HELP = "!rp1 walk|stop|left|right|follow|wave|dance|reset, "
            + "!rp1 here (anchor at your feet), !rp1 scale <0.25-10>";

    public final Kind kind;
    public final String cmd;
    public final String json;
    public final double scale;
    public final String message;

    private Rp1Command(Kind kind, String cmd, String json, double scale, String message) {
        this.kind = kind;
        this.cmd = cmd;
        this.json = json;
        this.scale = scale;
        this.message = message;
    }

    /** True if the chat line is addressed to RP1 (so it must not be sent to the server). */
    public static boolean isRp1(String line) {
        if (line == null) {
            return false;
        }
        String t = line.trim();
        int n = PREFIX.length();
        return t.regionMatches(true, 0, PREFIX, 0, n) && (t.length() == n || Character.isWhitespace(t.charAt(n)));
    }

    public static Rp1Command parse(String line) {
        if (!isRp1(line)) {
            return new Rp1Command(Kind.NOT_RP1, null, null, 0, null);
        }
        String[] a = line.trim().substring(PREFIX.length()).trim().toLowerCase(Locale.ROOT).split("\\s+");
        if (a.length == 0 || a[0].isEmpty() || a[0].equals("help") || a[0].equals("?")) {
            return new Rp1Command(Kind.HELP, "help", null, 0, HELP);
        }
        String c = a[0];
        switch (c) {
            case "walk":
            case "stop":
            case "follow":
            case "wave":
            case "dance":
            case "reset":
                return sidecar(c, "{\"cmd\":\"" + c + "\",\"from\":\"mc\"}");
            case "left":
            case "right":
                return sidecar("turn " + c, "{\"cmd\":\"turn\",\"dir\":\"" + c + "\",\"from\":\"mc\"}");
            case "here":
                return new Rp1Command(Kind.HERE, "here", null, 0, null);
            case "scale":
                if (a.length < 2) {
                    return error("usage: !rp1 scale <" + MIN_SCALE + "-" + MAX_SCALE + ">");
                }
                double s;
                try {
                    s = Double.parseDouble(a[1]);
                } catch (NumberFormatException e) {
                    return error("not a number: " + a[1]);
                }
                if (!(s >= MIN_SCALE && s <= MAX_SCALE)) {
                    return error("scale must be between " + MIN_SCALE + " and " + MAX_SCALE);
                }
                return new Rp1Command(Kind.SCALE, "scale", null, s, null);
            default:
                return error("unknown command '" + c + "'. " + HELP);
        }
    }

    private static Rp1Command sidecar(String cmd, String json) {
        return new Rp1Command(Kind.SIDECAR, cmd, json, 0, null);
    }

    private static Rp1Command error(String msg) {
        return new Rp1Command(Kind.ERROR, null, null, 0, msg);
    }
}
