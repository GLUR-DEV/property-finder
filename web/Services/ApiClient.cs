using System.Net.Http.Json;
using System.Text.Json;
using RentalFinder.Web.Models;

namespace RentalFinder.Web.Services;

/// <summary>An error from the Python API, with a message that is safe to show.</summary>
public class ApiException(string message) : Exception(message);

public class ApiClient(HttpClient http)
{
    public async Task<List<RegionDto>> GetLocationsAsync() =>
        await GetAsync<List<RegionDto>>("api/locations") ?? [];

    public async Task<List<string>> GetSiteRegionsAsync() =>
        await GetAsync<List<string>>("api/site/regions") ?? [];

    public async Task<List<string>> GetSiteDistrictsAsync(string region) =>
        await GetAsync<List<string>>($"api/site/districts?region={Uri.EscapeDataString(region)}") ?? [];

    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);

    /// <summary>Each listing keeps its full raw JSON in <see cref="Listing.Raw"/> for the detail view.</summary>
    public async Task<ListingsResult> GetListingsAsync()
    {
        var raw = await GetAsync<RawListingsResult>("api/listings");
        if (raw is null) return new ListingsResult();

        return new ListingsResult
        {
            SearchedAt = raw.SearchedAt,
            Url = raw.Url,
            Count = raw.Count,
            Listings = raw.Listings.Select(element =>
            {
                var listing = element.Deserialize<Listing>(Json) ?? new Listing();
                listing.Raw = element;
                return listing;
            }).ToList(),
        };
    }

    public Task<StatsResponse?> GetStatsAsync(string? scopeKey = null) =>
        GetAsync<StatsResponse>(scopeKey is null ? "api/stats" : $"api/stats?scope_key={Uri.EscapeDataString(scopeKey)}");

    public Task<JobDto> StartSearchAsync(SearchRequest request) => PostAsync("api/searches", request);

    public Task<JobDto> StartGrabAsync(string region, string district) =>
        PostAsync("api/locations/grab", new { region, district });

    public async Task<JobDto> GetJobAsync(string id) =>
        await GetAsync<JobDto>($"api/jobs/{id}") ?? throw new ApiException("Job not found.");

    /// <summary>Poll a job once a second until it finishes, reporting each update.</summary>
    public async Task<JobDto> WaitForJobAsync(JobDto job, Action<JobDto> onUpdate)
    {
        while (job.Running)
        {
            await Task.Delay(1000);
            job = await GetJobAsync(job.Id);
            onUpdate(job);
        }
        return job;
    }

    // ---- watches and notifications -----------------------------------

    public async Task<List<WatchDto>> GetWatchesAsync() =>
        await GetAsync<List<WatchDto>>("api/watches") ?? [];

    public Task<WatchDto> CreateWatchAsync(WatchRequest request) =>
        RequestAsync<WatchDto>(HttpMethod.Post, "api/watches", request);

    public Task<WatchDto> UpdateWatchAsync(int id, WatchRequest request) =>
        RequestAsync<WatchDto>(HttpMethod.Put, $"api/watches/{id}", request);

    public Task<WatchDto> SetWatchEnabledAsync(int id, bool enabled) =>
        RequestAsync<WatchDto>(HttpMethod.Patch, $"api/watches/{id}/enabled", new { enabled });

    public async Task DeleteWatchAsync(int id) =>
        await SendAsync(() => http.DeleteAsync($"api/watches/{id}"));

    public async Task<JobDto> RunWatchAsync(int id)
    {
        var response = await SendAsync(() => http.PostAsync($"api/watches/{id}/run", null));
        return await response.Content.ReadFromJsonAsync<JobDto>()
               ?? throw new ApiException("The API returned an empty response.");
    }

    public async Task<NotifyDto> GetNotificationsAsync() =>
        await GetAsync<NotifyDto>("api/notifications") ?? new NotifyDto("", "", false, false);

    public Task<NotifyDto> SaveNotificationsAsync(NotifyRequest request) =>
        RequestAsync<NotifyDto>(HttpMethod.Put, "api/notifications", request);

    public async Task SendTestNotificationAsync() =>
        await SendAsync(() => http.PostAsync("api/notifications/test", null));

    // ---- plumbing ----------------------------------------------------

    private async Task<T?> GetAsync<T>(string url)
    {
        var response = await SendAsync(() => http.GetAsync(url));
        return await response.Content.ReadFromJsonAsync<T>();
    }

    private async Task<T> RequestAsync<T>(HttpMethod method, string url, object body)
    {
        var response = await SendAsync(() => http.SendAsync(
            new HttpRequestMessage(method, url) { Content = JsonContent.Create(body, body.GetType()) }));
        return await response.Content.ReadFromJsonAsync<T>()
               ?? throw new ApiException("The API returned an empty response.");
    }

    private async Task<JobDto> PostAsync<TBody>(string url, TBody body)
    {
        var response = await SendAsync(() => http.PostAsJsonAsync(url, body));
        return await response.Content.ReadFromJsonAsync<JobDto>()
               ?? throw new ApiException("The API returned an empty response.");
    }

    private static async Task<HttpResponseMessage> SendAsync(Func<Task<HttpResponseMessage>> send)
    {
        HttpResponseMessage response;
        try
        {
            response = await send();
        }
        catch (HttpRequestException)
        {
            throw new ApiException("Can't reach the API. Start both parts with: python run.py. "
                + "If you did, check the [api] output for an error. To run the API alone, use "
                + "uvicorn main:app --port 8000 from the api folder.");
        }
        catch (TaskCanceledException)
        {
            throw new ApiException("The API took too long to respond.");
        }

        if (!response.IsSuccessStatusCode)
            throw new ApiException(await ReadErrorAsync(response));
        return response;
    }

    // FastAPI errors are {"detail": "text"} or, for validation, {"detail": [{"msg": "..."}]}
    private static async Task<string> ReadErrorAsync(HttpResponseMessage response)
    {
        var fallback = $"The API returned an error ({(int)response.StatusCode}).";
        try
        {
            using var doc = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
            if (!doc.RootElement.TryGetProperty("detail", out var detail)) return fallback;

            if (detail.ValueKind == JsonValueKind.String) return detail.GetString() ?? fallback;
            if (detail.ValueKind == JsonValueKind.Array && detail.GetArrayLength() > 0
                && detail[0].TryGetProperty("msg", out var msg))
                return (msg.GetString() ?? fallback).Replace("Value error, ", "");
        }
        catch (JsonException) { }
        return fallback;
    }
}
