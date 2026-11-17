namespace RentalFinder.Web.Models;

// Shapes returned by GET /api/stats. Property names match the API's camelCase JSON.

public record ScopeInfo(string ScopeKey, string Label, int Runs, DateTimeOffset FirstRun, DateTimeOffset LastRun, int LastCount);

public record HistBin(double From, double To, int Count);
public record RentStats(double? Median, double? Mean, double? Min, double? Max, List<HistBin> Histogram);

public record SuburbStat(string Suburb, int Count, double Median, double PerBedroom);
public record GroupStat(string Label, int Count, double Min, double Median, double Max);

public record WeekCount(string WeekStart, int Count);
public record LabelCount(string Label, int Count);
public record SupplyStats(
    List<WeekCount> NewPerWeek, int NewOlder,
    List<WeekCount> Availability, int AvailablePast, int AvailableLater,
    List<LabelCount> Ages, double? MedianAgeDays);

public record ListerGroup(int Count, double Share, double? MedianRent);
public record AgencyStat(string Name, int Count, double? MedianRent);
public record ListerStats(ListerGroup PrivateListers, ListerGroup AgencyListers, List<AgencyStat> TopAgencies);

public record RunPoint(
    DateTimeOffset SearchedAt, int Count, double? MedianRent, double? MeanRent, double PrivateShare,
    int? New, int? Removed, int? PriceChanged, double? RemovedMedianDaysListed);
public record Series(string Name, List<double?> Values);
public record TrendStats(List<RunPoint> Runs, List<Series> ByBedrooms, List<Series> BySuburb);

public record StatsResponse(
    bool HasData, List<ScopeInfo>? Scopes, ScopeInfo? Scope, DateTimeOffset? SearchedAt, int Count,
    RentStats? Rent, List<SuburbStat>? BySuburb, List<GroupStat>? ByBedrooms, List<GroupStat>? ByType,
    SupplyStats? Supply, ListerStats? Listers, TrendStats? Trends);

/// <summary>One column or bar in a chart. Tip is the hover text.</summary>
public record ChartItem(string Label, double Value, string? Tip = null);
