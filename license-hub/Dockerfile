FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /src
COPY LicenseHub.csproj ./
RUN dotnet restore LicenseHub.csproj
COPY . ./
RUN dotnet publish LicenseHub.csproj --configuration Release --no-restore --output /app/publish

FROM mcr.microsoft.com/dotnet/aspnet:8.0 AS runtime
WORKDIR /app
COPY --from=build /app/publish ./
ENV ASPNETCORE_URLS=http://+:8080
EXPOSE 8080
USER $APP_UID
ENTRYPOINT ["dotnet", "LicenseHub.dll"]
