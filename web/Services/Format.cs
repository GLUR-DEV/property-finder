using System.Globalization;
using System.Text.Json;
using RentalFinder.Web.Models;

namespace RentalFinder.Web.Services;

/// <summary>Small display helpers shared by the pages.</summary>
public static class Format
{
    /// <summary>"manukau-city" -> "Manukau City"</summary>
    public static string Pretty(string slug) =>
        CultureInfo.CurrentCulture.TextInfo.ToTitleCase(slug.Replace('-', ' '));

    public static string Beds(int? n) => n switch { null => "", 0 => "Studio", _ => $"{n} bed" };

    public static string Baths(int? n) => n is null ? "" : $"{n} bath";

    /// <summary>"Royal Oak, Auckland City" - parts that are missing are left out, with no stray commas.</summary>
    public static string Place(params string?[] parts) =>
        string.Join(", ", parts.Where(p => !string.IsNullOrWhiteSpace(p)));

    public static string Rent(Listing l) =>
        l.RentPerWeek is { } r ? "$" + r.ToString("#,0.##", CultureInfo.InvariantCulture) : (l.PriceDisplay ?? "No price");

    /// <summary>
    /// How long a listing had been up, measured at <paramref name="asOf"/> (the time of the search, so it
    /// matches the Statistics page). Defaults to now.
    /// </summary>
    public static string Ago(DateTimeOffset? date, DateTimeOffset? asOf = null)
    {
        if (date is not { } d) return "";
        var days = (int)((asOf ?? DateTimeOffset.Now) - d).TotalDays;
        return days switch { <= 0 => "Listed today", 1 => "Listed yesterday", _ => $"Listed {days} days ago" };
    }

    /// <summary>
    /// Flatten a listing's JSON into (field, value) rows the way listings.csv does:
    /// nested objects become dotted names (geographicLocation.latitude) and list
    /// items get an index (agency.agents[0].fullName). Empty values are kept.
    /// </summary>
    public static List<KeyValuePair<string, string>> Flatten(JsonElement element)
    {
        var rows = new List<KeyValuePair<string, string>>();
        Walk(element, "", rows);
        return rows;
    }

    private static void Walk(JsonElement e, string path, List<KeyValuePair<string, string>> rows)
    {
        switch (e.ValueKind)
        {
            case JsonValueKind.Object:
                var any = false;
                foreach (var property in e.EnumerateObject())
                {
                    any = true;
                    Walk(property.Value, path == "" ? property.Name : $"{path}.{property.Name}", rows);
                }
                if (!any && path != "") rows.Add(new(path, "{}"));
                break;

            case JsonValueKind.Array:
                var index = 0;
                foreach (var item in e.EnumerateArray())
                    Walk(item, $"{path}[{index++}]", rows);
                if (index == 0) rows.Add(new(path, "[]"));
                break;

            case JsonValueKind.String:
                rows.Add(new(path, e.GetString() ?? ""));
                break;

            case JsonValueKind.Null:
            case JsonValueKind.Undefined:
                rows.Add(new(path, ""));
                break;

            default: // numbers, true, false
                rows.Add(new(path, e.GetRawText()));
                break;
        }
    }
}
