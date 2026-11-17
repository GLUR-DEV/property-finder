using System.Globalization;
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

    public static string Rent(Listing l) =>
        l.RentPerWeek is { } r ? "$" + r.ToString("#,0.##", CultureInfo.InvariantCulture) : (l.PriceDisplay ?? "No price");

    public static string Ago(DateTimeOffset? date)
    {
        if (date is not { } d) return "";
        var days = (int)(DateTimeOffset.Now - d).TotalDays;
        return days switch { <= 0 => "Listed today", 1 => "Listed yesterday", _ => $"Listed {days} days ago" };
    }
}
