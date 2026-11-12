FROM --platform=$BUILDPLATFORM mcr.microsoft.com/dotnet/sdk:8.0 AS build
ARG TARGETARCH
WORKDIR /src
COPY LicenseHub.csproj ./
RUN dotnet restore LicenseHub.csproj -a $TARGETARCH
COPY . ./
RUN dotnet publish LicenseHub.csproj -a $TARGETARCH --configuration Release --no-restore --output /app/publish -m:1 -p:UseAppHost=false

FROM mcr.microsoft.com/dotnet/aspnet:8.0 AS runtime
ARG IMAGE_VERSION=0.1.0-admin.1
ARG VCS_REF=unknown
WORKDIR /app
COPY --from=build /app/publish ./
ENV ASPNETCORE_URLS=http://+:8080
ENV DOTNET_EnableDiagnostics=0
LABEL org.opencontainers.image.title="AgentForge License Admin" \
      org.opencontainers.image.source="https://github.com/jtuongdang/agentforge" \
      org.opencontainers.image.version=$IMAGE_VERSION \
      org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.description="License, device and user administration with device-bound entitlements"
EXPOSE 8080
USER $APP_UID
HEALTHCHECK --interval=30s --timeout=6s --start-period=30s --retries=3 CMD ["dotnet", "LicenseHub.dll", "--healthcheck"]
ENTRYPOINT ["dotnet", "LicenseHub.dll"]
