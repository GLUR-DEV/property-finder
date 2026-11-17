using RentalFinder.Web.Components;
using RentalFinder.Web.Services;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddRazorComponents().AddInteractiveServerComponents();

// The Python API. Change Api:BaseUrl in appsettings.json if you run it elsewhere.
builder.Services.AddHttpClient<ApiClient>(client =>
{
    client.BaseAddress = new Uri(builder.Configuration["Api:BaseUrl"] ?? "http://127.0.0.1:8000");
    client.Timeout = TimeSpan.FromMinutes(2); // opening Trade Me for regions/districts can be slow
});

var app = builder.Build();

app.UseStaticFiles();
app.UseAntiforgery();
app.MapRazorComponents<App>().AddInteractiveServerRenderMode();

app.Run();
