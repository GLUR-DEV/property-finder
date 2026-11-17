using System.Globalization;

namespace RentalFinder.Web.Services;

/// <summary>Number, axis and label helpers for the SVG charts. Use with `using static`.</summary>
public static class ChartMath
{
    private static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

    /// <summary>A number for an SVG attribute: always a dot decimal, whatever the culture.</summary>
    public static string F(double v) => v.ToString("0.##", Inv);

    public static string Money(double v) => "$" + v.ToString("#,0", Inv);
    public static string Num(double v) => v.ToString("#,0.##", Inv);
    public static string Pct(double share) => (share * 100).ToString("0", Inv) + "%";

    public static string Truncate(string text, int max) => text.Length <= max ? text : text[..(max - 1)] + "…";

    public static string Day(DateTimeOffset d) => d.ToLocalTime().ToString("d MMM", Inv);
    public static string Day(string isoDate) => DateTime.Parse(isoDate, Inv).ToString("d MMM", Inv);

    /// <summary>A round step (1, 2, 5, 10 ...) that gives about four ticks across the range.</summary>
    public static double NiceStep(double raw)
    {
        if (raw <= 0) return 1;
        var magnitude = Math.Pow(10, Math.Floor(Math.Log10(raw)));
        var n = raw / magnitude;
        return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * magnitude;
    }

    /// <summary>Axis bounds and tick step for the given data range.</summary>
    /// <param name="zero">Start the axis at zero (counts) rather than near the data (rents).</param>
    /// <param name="whole">Only whole-number ticks (counts of listings).</param>
    public static (double Lo, double Hi, double Step) Axis(double min, double max, bool zero, bool whole)
    {
        if (zero) min = 0;
        else if (max - min < 1e-9) { min *= 0.95; max *= 1.05; }   // a flat line needs some air
        else { var pad = (max - min) * 0.15; min = Math.Max(0, min - pad); max += pad; }
        if (max - min < 1e-9) max = min + 1;

        var step = NiceStep((max - min) / 4);
        if (whole) step = Math.Max(1, step);
        return (Math.Floor(min / step) * step, Math.Ceiling(max / step) * step, step);
    }
}
