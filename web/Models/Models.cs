using System.Globalization;

namespace RentalFinder.Web.Models;

// ---- locations -------------------------------------------------------

public record SuburbDto(string Id, string Name);
public record DistrictDto(string District, List<SuburbDto> Suburbs);
public record RegionDto(string Region, List<DistrictDto> Districts);

// ---- jobs and requests ----------------------------------------------

public record JobDto(string Id, string Kind, string Status, string Message, int Page, int Count, string? Error)
{
    public bool Running => Status == "running";
    public bool Failed => Status == "error";
}

public class SearchRequest
{
    public string Region { get; set; } = "";
    public string District { get; set; } = "";
    public List<string> SuburbIds { get; set; } = [];
    public int? PriceMin { get; set; }
    public int? PriceMax { get; set; }
    public int? BedroomsMin { get; set; }
    public int? BedroomsMax { get; set; }
    public int? BathroomsMin { get; set; }
    public int? BathroomsMax { get; set; }
    public List<string> PropertyTypes { get; set; } = [];
}

// ---- listings (a subset of the fields Trade Me returns) --------------

public class ListingsResult
{
    public DateTimeOffset? SearchedAt { get; set; }
    public string? Url { get; set; }
    public int Count { get; set; }
    public List<Listing> Listings { get; set; } = [];
}

public class GeoPoint
{
    public double Latitude { get; set; }
    public double Longitude { get; set; }
}

public class AgencyInfo
{
    public string? Name { get; set; }
}

public class Listing
{
    public long ListingId { get; set; }
    public string? Title { get; set; }
    public string? Address { get; set; }
    public string? Suburb { get; set; }
    public string? District { get; set; }
    public string? Region { get; set; }
    public string? PropertyType { get; set; }
    public decimal? RentPerWeek { get; set; }
    public string? PriceDisplay { get; set; }
    public int? Bedrooms { get; set; }
    public int? Bathrooms { get; set; }
    public string? Parking { get; set; }
    public string? AvailableFrom { get; set; }   // e.g. 19/10/2026
    public string? StartDate { get; set; }       // ISO timestamp
    public string? PictureHref { get; set; }
    public List<string>? PhotoUrls { get; set; }
    public string? CanonicalPath { get; set; }
    public GeoPoint? GeographicLocation { get; set; }
    public AgencyInfo? Agency { get; set; }

    public string TradeMeUrl =>
        string.IsNullOrEmpty(CanonicalPath) ? "" : "https://www.trademe.co.nz" + CanonicalPath;

    public string MapUrl
    {
        get
        {
            if (GeographicLocation is not { } g) return "";
            var lat = g.Latitude.ToString(CultureInfo.InvariantCulture);
            var lon = g.Longitude.ToString(CultureInfo.InvariantCulture);
            return $"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=16/{lat}/{lon}";
        }
    }

    public DateTime? AvailableDate =>
        DateTime.TryParseExact(AvailableFrom, "d/M/yyyy", CultureInfo.InvariantCulture,
            DateTimeStyles.None, out var d) ? d : null;

    public DateTimeOffset? Listed =>
        DateTimeOffset.TryParse(StartDate, CultureInfo.InvariantCulture,
            DateTimeStyles.None, out var d) ? d : null;
}
