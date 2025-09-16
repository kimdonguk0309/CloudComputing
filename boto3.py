import boto3
import time

aws_region = 'ap-northeast-2' 
availability_zones = ['ap-northeast-2a', 'ap-northeast-2b']
ec2 = boto3.client('ec2', region_name=aws_region)

print("1. VPC 생성 중...")
try:
    vpc_response = ec2.create_vpc(CidrBlock='10.100.0.0/16')
    vpc_id = vpc_response['Vpc']['VpcId']
    ec2.create_tags(Resources=[vpc_id], Tags=[{'Key': 'Name', 'Value': 'skills-vpc'}])
    print(f"VPC 'skills-vpc' ({vpc_id}) 생성 완료.")
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={'Value': True})
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={'Value': True})

except Exception as e:
    print(f"VPC 생성 중 오류 발생: {e}")

# --- 2. Internet Gateway (IGW) 생성 및 연결 ---
print("\n2. Internet Gateway 생성 및 연결 중...")
try:
    igw_response = ec2.create_internet_gateway()
    igw_id = igw_response['InternetGateway']['InternetGatewayId']
    ec2.create_tags(Resources=[igw_id], Tags=[{'Key': 'Name', 'Value': 'skills-igw'}])
    ec2.attach_internet_gateway(VpcId=vpc_id, InternetGatewayId=igw_id)
    print(f"Internet Gateway 'skills-igw' ({igw_id}) 생성 및 VPC에 연결 완료.")
except Exception as e:
    print(f"IGW 생성/연결 중 오류 발생: {e}")

# --- 3. Subnet 생성 ---
print("\n3. Subnet 생성 중...")
subnet_details = [
    {'Name': 'skills-public-subnet-a', 'Cidr': '10.100.1.0/24', 'AZ': availability_zones[0]},
    {'Name': 'skills-public-subnet-b', 'Cidr': '10.100.2.0/24', 'AZ': availability_zones[1]},
    {'Name': 'skills-private-subnet-a', 'Cidr': '10.100.11.0/24', 'AZ': availability_zones[0]},
    {'Name': 'skills-private-subnet-b', 'Cidr': '10.100.12.0/24', 'AZ': availability_zones[1]},
    {'Name': 'skills-protected-subnet-a', 'Cidr': '10.100.21.0/24', 'AZ': availability_zones[0]},
    {'Name': 'skills-protected-subnet-b', 'Cidr': '10.100.22.0/24', 'AZ': availability_zones[1]},
]
subnets_info = {} # 서브넷 ID를 저장할 딕셔너리

for detail in subnet_details:
    try:
        subnet_response = ec2.create_subnet(
            VpcId=vpc_id,
            CidrBlock=detail['Cidr'],
            AvailabilityZone=detail['AZ']
        )
        subnet_id = subnet_response['Subnet']['SubnetId']
        ec2.create_tags(Resources=[subnet_id], Tags=[{'Key': 'Name', 'Value': detail['Name']}])
        subnets_info[detail['Name']] = subnet_id
        print(f"Subnet '{detail['Name']}' ({subnet_id}) 생성 완료.")

        # Public Subnet의 경우 Public IP 자동 할당 활성화 (Bastion EC2 위함)
        if 'public' in detail['Name']:
            ec2.g_modify_subnet_attribute(SubnetId=subnet_id, MapPublicIpOnLaunch={'Value': True})
            print(f"  -> '{detail['Name']}'에 MapPublicIpOnLaunch 활성화.")

    except Exception as e:
        print(f"Subnet '{detail['Name']}' 생성 중 오류 발생: {e}")

# --- 4. NAT Gateway 생성 (각 Public Subnet에 하나씩) ---
print("\n4. NAT Gateway 생성 중...")
nat_gateway_info = {}

for az_suffix, public_subnet_name in [('a', 'skills-public-subnet-a'), ('b', 'skills-public-subnet-b')]:
    try:
        # Elastic IP 할당
        eip_alloc_response = ec2.allocate_address(Domain='vpc')
        eip_alloc_id = eip_alloc_response['AllocationId']
        print(f"Elastic IP ({eip_alloc_id}) 할당 완료 for skills-nat-{az_suffix}.")

        # NAT Gateway 생성
        nat_gw_response = ec2.create_nat_gateway(
            SubnetId=subnets_info[public_subnet_name],
            AllocationId=eip_alloc_id
        )
        nat_gw_id = nat_gw_response['NatGateway']['NatGatewayId']
        ec2.create_tags(Resources=[nat_gw_id], Tags=[{'Key': 'Name', 'Value': f'skills-nat-{az_suffix}'}])
        nat_gateway_info[f'skills-nat-{az_suffix}'] = nat_gw_id
        print(f"NAT Gateway 'skills-nat-{az_suffix}' ({nat_gw_id}) 생성 시작. (약간의 시간 소요)")

        # NAT Gateway가 사용 가능해질 때까지 대기
        waiter = ec2.get_waiter('nat_gateway_available')
        waiter.wait(NatGatewayIds=[nat_gw_id])
        print(f"NAT Gateway 'skills-nat-{az_suffix}' 사용 가능 상태.")

    except Exception as e:
        print(f"NAT Gateway 'skills-nat-{az_suffix}' 생성 중 오류 발생: {e}")

# --- 5. Route Tables 생성 및 라우팅 설정 ---
print("\n5. Route Tables 생성 및 라우팅 설정 중...")

# Public Route Table
print("  - Public Route Table 생성 및 설정 중...")
public_rtb_response = ec2.create_route_table(VpcId=vpc_id)
public_rtb_id = public_rtb_response['RouteTable']['RouteTableId']
ec2.create_tags(Resources=[public_rtb_id], Tags=[{'Key': 'Name', 'Value': 'skills-public-rtb'}])
ec2.create_route(
    RouteTableId=public_rtb_id,
    DestinationCidrBlock='0.0.0.0/0',
    GatewayId=igw_id
)
ec2.associate_route_table(SubnetId=subnets_info['skills-public-subnet-a'], RouteTableId=public_rtb_id)
ec2.associate_route_table(SubnetId=subnets_info['skills-public-subnet-b'], RouteTableId=public_rtb_id)
print(f"  Public Route Table ({public_rtb_id}) 생성 및 연결 완료.")


# Private Route Table A
print("  - Private Route Table A 생성 및 설정 중...")
private_rtb_a_response = ec2.create_route_table(VpcId=vpc_id)
private_rtb_a_id = private_rtb_a_response['RouteTable']['RouteTableId']
ec2.create_tags(Resources=[private_rtb_a_id], Tags=[{'Key': 'Name', 'Value': 'skills-private-rtb-a'}])
ec2.create_route(
    RouteTableId=private_rtb_a_id,
    DestinationCidrBlock='0.0.0.0/0',
    NatGatewayId=nat_gateway_info['skills-nat-a']
)
ec2.associate_route_table(SubnetId=subnets_info['skills-private-subnet-a'], RouteTableId=private_rtb_a_id)
print(f"  Private Route Table A ({private_rtb_a_id}) 생성 및 연결 완료.")

# Private Route Table B
print("  - Private Route Table B 생성 및 설정 중...")
private_rtb_b_response = ec2.create_route_table(VpcId=vpc_id)
private_rtb_b_id = private_rtb_b_response['RouteTable']['RouteTableId']
ec2.create_tags(Resources=[private_rtb_b_id], Tags=[{'Key': 'Name', 'Value': 'skills-private-rtb-b'}])
ec2.create_route(
    RouteTableId=private_rtb_b_id,
    DestinationCidrBlock='0.0.0.0/0',
    NatGatewayId=nat_gateway_info['skills-nat-b']
)
ec2.associate_route_table(SubnetId=subnets_info['skills-private-subnet-b'], RouteTableId=private_rtb_b_id)
print(f"  Private Route Table B ({private_rtb_b_id}) 생성 및 연결 완료.")

# Protected Route Table (NO INTERNET ACCESS)
print("  - Protected Route Table 생성 및 설정 중 (인터넷 접근 없음)...")
protected_rtb_response = ec2.create_route_table(VpcId=vpc_id)
protected_rtb_id = protected_rtb_response['RouteTable']['RouteTableId']
ec2.create_tags(Resources=[protected_rtb_id], Tags=[{'Key': 'Name', 'Value': 'skills-protected-rtb'}])
# Protected Subnet은 기본적으로 인터넷 접근이 없으므로, 0.0.0.0/0 라우팅을 추가하지 않습니다.
ec2.associate_route_table(SubnetId=subnets_info['skills-protected-subnet-a'], RouteTableId=protected_rtb_id)
ec2.associate_route_table(SubnetId=subnets_info['skills-protected-subnet-b'], RouteTableId=protected_rtb_id)
print(f"  Protected Route Table ({protected_rtb_id}) 생성 및 연결 완료.")


print("\n네트워킹 구성 완료!")
print(f"생성된 VPC ID: {vpc_id}")
print(f"생성된 Subnets: {subnets_info}")
print(f"생성된 Internet Gateway ID: {igw_id}")
print(f"생성된 NAT Gateways: {nat_gateway_info}")
print(f"생성된 Public RT ID: {public_rtb_id}")
print(f"생성된 Private RT A ID: {private_rtb_a_id}")
print(f"생성된 Private RT B ID: {private_rtb_b_id}")
print(f"생성된 Protected RT ID: {protected_rtb_id}")


